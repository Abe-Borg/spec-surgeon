"""Tier B — a generated transform for a request no op can express.

Offered when it is needed (decided 2026-10-15): when the conversation ends with ``unsupported`` and Claude judged the
request within the catalog's ``custom_transform_limits``, the conversation offers a one-off custom transform. The
window takes that offer itself (``document_task.DocumentTask`` acting on ``Session.outcome``): the user sees the
result as a plan, a preview and the one action, never the offer. There is no other way in: the command line was
removed on 2026-10-21, and a test drives ``Session.reply`` with the choice. ``generate_op`` gives
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

**Obliterate mode** (``unguarded``, CLAUDE.md, Modes; never on a governed goal path): step 3 is
skipped and the writer's prompt says any change Word stores is allowed; the whitelist (1) and the sandbox (2) are
unchanged; the suite (4) still runs on every sample and every file, and a trial whose only failures are its checks
— the verifier read the output, the source did not drift — passes with them listed (``FileTrial.unverified``),
shown to the user and delivered with the copy.

A passing op is saved (``generated_ops/<hash>.py`` + ``.history.json`` beside the batch's records) — a cache, not
a registered op: promotion into ``ops/`` is a human decision, with tests. ``run_generated`` applies it to a batch
the same way (sandbox per file, verifier per file); outputs go to the output folder only — never in place.

``codegen/history.json`` is the diagnosis of every attempt, passing or not: the submitted source, its problems and
trials, and the whole exchange with the model (``GeneratedOp.transcript``), re-written after every turn and on every
way out. An API error (``llm.is_api_error``) ends the generation as a recorded failure; anything else propagates.

**The spending check** (``llm.BudgetExceeded`` from a call the user's check refuses, ``DocumentTask._new_request``)
is a pause, not a failure: the writer stops with ``GeneratedOp.budget_stop`` set and its whole state on disk — the
exchange after its last answered turn in the history checkpoint (``contexts.checkpoint_history``, one per model call,
the same record recovery resumes from) and the rest in ``history.json`` (``calls``, ``attempts``, ``usage``,
``errors``, ``check_rounds``, the samples). ``generate_op(resume_from=)`` names that folder: ``load_writer_checkpoint``
reads it back, and the loop goes on from there — the same brief, pins, contract and approved words, the same folder,
the call and attempt counts continuing, so the model keeps everything it had looked at (live QA finding F09: a second
writer started from the brief alone and repeated nine inspections). A checkpoint that cannot be continued (missing,
written for another brief, sample set, pins or instructions, or not a pause) is logged and the writer starts afresh.
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
from .resources import (CODEGEN_EFFORT, DEFAULT_CONTEXT_TOKENS, DEFAULT_MAX_ATTEMPTS, DEFAULT_MAX_CALLS,
                        DEFAULT_MAX_OUTPUT_TOKENS, DEFAULT_STALL_REPETITIONS, InvestigationProgress, positive_integer)

ALLOWED_FROM: Dict[str, set] = {
    "spec_surgeon.ops.base": {"Op", "PlanFragment", "BlockDiff", "JobException"},
    "spec_surgeon.package": {"W", "NS", "R", "DocxPackage", "serialize_xml", "parse_xml"},
    "spec_surgeon.ooxml": {"PPR_ORDER", "RPR_ORDER", "SECTPR_ORDER", "STYLE_ORDER", "TBLPR_ORDER", "TRPR_ORDER",
                           "TCPR_ORDER", "insert_in_order", "get_or_add",
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
                               "display_segments", "expected_block_text", "mark_slots", "nearest_p", "paragraph_runs", "reference_values",
                               "rule_uses_literal"},
    "spec_surgeon.ooxml": {"RevisionStamp", "property_change", "max_revision_id", "file_uses_date_utc"},
    "spec_surgeon.ops.textedit": {"Edit", "RunText", "run_text", "plan_paragraph", "edits_to_data",
                                  "edits_from_data", "apply_part_edits"},
}


def allowed_imports(goal_contract=None, unguarded: bool = False):
    """The import whitelist: plain formatting by default; a governed method (``goal_contract``) and an Obliterate
    method (``unguarded``: any change Word stores) also get the ``GOAL_FROM``
    helpers — text edits, tracked revisions, parts and relationships — under the same sandbox."""
    allowed = {name: set(values) for name, values in ALLOWED_FROM.items()}
    if goal_contract is not None:
        from .goals import validate_contract
        validate_contract(goal_contract)
    if goal_contract is not None or unguarded:
        for name, values in GOAL_FROM.items():
            allowed.setdefault(name, set()).update(values)
    return allowed
# What plain (ungoverned) formatting methods may not import: tracked-formatting helpers and the engine's registration hook.
# Governed and Obliterate methods have the additional GOAL_FROM helpers. tests/test_codegen.py pins that every name
# the examples import is either allowed above or listed here.
EXCLUDED_FROM_EXAMPLES: Dict[str, set] = {
    "spec_surgeon.ooxml": {"RevisionStamp", "property_change", "max_revision_id", "file_uses_date_utc"},
    "spec_surgeon.ops.replace_text": {"default_author"},
    # ``register`` enters the op library; ``prose`` and set_font's phrase helpers write the sentence a built-in op
    # shows in the plan (``describe``). A generated one-off transform is neither registered nor described — the window
    # shows its brief instead.
    "spec_surgeon.ops": {"register", "prose"},
    "spec_surgeon.ops.set_font": {"style_definitions", "tracked_phrase"},
    # set_page_setup's built-in batch 5 notes and reads (what a first page shows through inheritance, what a content
    # selection of sections reads): a generated op neither selects sections by content nor flags their headers
    "spec_surgeon.ops.hf_common": {"effective_refs", "refs"},
    "spec_surgeon.ops.selectors": {"section_reads"},
    # Numbering fonts require a whole-member built-in proof; plain generated formatting covers runs only.
    "spec_surgeon.ops.font_numbering": {"NUMBERING_PARAM", "apply_numbering", "numbering_enabled", "plan_numbering"},
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


def check_source(src: str, *, goal_contract=None, unguarded: bool = False) -> List[str]:
    """Whitelist problems of a generated op's source ([] = acceptable)."""
    try:
        allowed = allowed_imports(goal_contract, unguarded)
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
                   source_bytes: Optional[bytes] = None, unguarded: bool = False,
                   attachments: Sequence[Dict[str, str]] = ()) -> SandboxResult:
    """plan + apply of the generated op on a COPY of ``docx`` in a separate isolated process. ``unguarded`` (an
    Obliterate method) widens the whitelist the runner re-checks; ``attachments`` (Obliterate: the files the user
    attached, each ``{"path", "sha256"}`` pinned when the method was written) reach the method as ``pkg.inputs``."""
    limits = {"seconds": timeout, "memory_mb": memory_mb, "output_bytes": output_bytes, "log_bytes": log_bytes}
    for name, value in limits.items():
        positive_integer(name, value)
    if source_bytes is not None and not isinstance(source_bytes, bytes):
        raise TypeError("source_bytes must be immutable bytes")
    with diag.scope(sandbox_id=diag.new_id()), \
            diag.span("codegen.sandbox", source=str(docx), workdir=str(workdir), timeout_seconds=timeout) as record:
        result = _run_in_sandbox(source, docx, select, params, workdir, timeout, goal_contract, limits, source_bytes,
                                 goal_binding, unguarded and goal_contract is None, attachments)
        record.update(ok=result.ok, code=result.code, output=str(result.output) if result.output else None)
        return result


def _run_in_sandbox(source, docx, select, params, workdir, timeout, goal_contract, limits, source_bytes,
                    goal_binding=None, unguarded=False, attachments=()):
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
    elif unguarded:
        # Obliterate: the files the user attached, as the bytes pinned when the method was written — never re-read
        try:
            for index, pin in enumerate(attachments or ()):
                path = Path(pin["path"]).resolve()
                data = path.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                if digest != pin["sha256"]:
                    raise ValueError(f"the attached file {path.name} changed since the custom method was written")
                snapshot = workdir / f"input-{index}{path.suffix.lower()}"
                snapshot.write_bytes(data)
                inputs.append({"path": str(path), "aliases": [str(path)], "snapshot": str(snapshot), "sha256": digest})
        except (ValueError, OSError, KeyError, TypeError) as exc:
            return SandboxResult(False, error=str(exc), code="stale-input", seconds=time.perf_counter() - t0)
    req = workdir / "request.json"
    req.write_text(json.dumps({"source": source, "docx": str(copy_path), "select": select, "params": params,
                              "goal_contract": goal_contract, "goal_binding": goal_binding,
                              "inputs": inputs, "limits": limits, "unguarded": bool(unguarded),
                              "author": _tracked_author() if unguarded else None}),
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


def _tracked_author() -> str:
    """The author an Obliterate method's tracked changes carry (``pkg.author``): the one the built-in ops use."""
    from .ops.replace_text import default_author
    try:
        return default_author()
    except Exception:  # noqa: BLE001 — no user name on this account: Word's own fallback
        return "Spec Surgeon"


def hard_limits(plan: Dict[str, Any], allowed_parts: Optional[Sequence[str]] = None, *, goal_contract=None) -> List[str]:
    """Check a generated plan before the suite runs; governed methods also face the independent final-goal check.
    Plain formatting (``allowed_parts``): the source's
    text parts and its styles part — the parts V10's residual identity covers — and the numbering part a
    ``numbering_labels`` assertion names (V10 proves that member whole); any other part (docProps, a chart…) could
    carry a change no verified assertion checks, so it is refused outright."""
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
        # list labels (A3): the numbering part a numbering_labels assertion names, which V10 proves whole — that it is
        # the document's numbering part included
        allowed = set(allowed_parts) | {a.get("numbering_part") for a in plan.get("property_assertions") or []
                                         if a.get("kind") == "numbering_labels" and a.get("numbering_part")}
        outside = sorted({p for p in list(plan.get("intended_parts") or []) + sorted(removed) if p not in allowed})
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
    unverified: List[str] = field(default_factory=list)   # Obliterate: the failed checks it passed with

    def to_dict(self):
        return {"source": str(self.source), "passed": self.passed, "problems": self.problems,
                "output": str(self.output) if self.output else None, "output_md5": self.output_md5,
                "identity": self.identity.to_dict() if self.identity is not None else None,
                "goal_binding": self.goal_binding, "unverified": list(self.unverified)}


def trial(source: str, f: Path, select, params, workdir: Path, *, goal_contract=None, goal_binding=None,
          unguarded: bool = False, attachments: Sequence[Dict[str, str]] = (),
          accepted: Optional[Dict[str, str]] = None) -> FileTrial:
    """``f`` through the sandbox and the full suite. Obliterate (``unguarded``): ``attachments`` reach the method, and
    a failed check the writer kept on purpose (``accepted``: check id → its reason) is listed with that reason."""
    with diag.scope(trial_id=diag.new_id()), \
            diag.span("codegen.trial", source=str(f), workdir=str(workdir)) as record:
        result = _trial(source, f, select, params, workdir, goal_contract, goal_binding,
                        unguarded and goal_contract is None, attachments, accepted)
        record.update(passed=result.passed, problem_count=len(result.problems), output_md5=result.output_md5,
                      failed_checks=[c.id for c in result.record.checks if c.status == "FAIL"]
                      if result.record is not None else [])
        return result


def _trial(source, f, select, params, workdir, goal_contract, goal_binding, unguarded=False, attachments=(),
           accepted=None):
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
                        goal_binding=goal_binding, source_bytes=original, unguarded=unguarded,
                        attachments=attachments if unguarded else ())
    if not sb.ok:
        where = _tail(sb.traceback, 12)
        return failed([f"[{sb.code or 'error'}] {sb.error}"
                       + (f"\n(traceback, last lines:)\n{where}" if where else "")])
    try:
        with diag.span("codegen.trial.limits") as record:
            limits = [] if unguarded else hard_limits(sb.plan, allowed_parts_of(f, source_bytes=original),
                                                      goal_contract=goal_contract)
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
    if unguarded and fails and not drift:
        # Obliterate: the checks report, they do not stop the copy; one the writer kept on purpose says why
        kept = accepted or {}
        return FileTrial(f, True, [], sb.output, rec, hashlib.md5(out_bytes).hexdigest(), identity, goal_binding,
                         unverified=[f"{c.id} ({c.name})" + (f" — Claude says this is part of the change: {kept[c.id]}"
                                                             if kept.get(c.id) else "")
                                     for c in rec.checks if c.status == "FAIL"])
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
    unguarded: bool = False                   # written in Obliterate mode: no plain-formatting limits (module docstring)
    # Obliterate only: the files the user attached, pinned when the method was written ({"path", "sha256"}; the
    # sandbox refuses one that changed), and the failed checks the writer kept on purpose (check id → its reason)
    attachments: List[Dict[str, str]] = field(default_factory=list)
    accepted_failures: Dict[str, str] = field(default_factory=dict)
    # The spending check paused the writer (``llm.BudgetExceeded``): its state is on disk and ``generate_op(resume_from=)``
    # continues it; ``calls`` is the number of calls made so far (a resumed writer counts on from it) and
    # ``check_rounds`` how many of Obliterate's ``UNGUARDED_CHECK_ROUNDS`` it has used
    budget_stop: bool = False
    calls: int = 0
    check_rounds: int = 0


class ResumeMismatch(ValueError):
    """A paused writer's folder that cannot be continued from (``generate_op(resume_from=)`` then starts afresh and
    says why)."""


def _executable_source(src: str) -> str:
    """The submitted module as the stall guard compares it: its syntax tree (comments and explanations change nothing),
    or the text itself when it does not parse."""
    try:
        return ast.dump(ast.parse(src), include_attributes=False)
    except (SyntaxError, ValueError, TypeError):
        return src


def load_writer_checkpoint(folder: Path, *, out_dir: Path, first_message: str, samples: Sequence[Path], system,
                           tools, source_pins) -> Dict[str, Any]:
    """The state of a writer the spending check paused in ``folder`` (``GeneratedOp.budget_stop``), read back for
    ``generate_op(resume_from=)``: the exchange after its last answered turn from the history checkpoint
    (``contexts.load_context_manifest``; a trailing assistant message, a tool call the paused call never answered, is
    dropped), and from ``history.json`` the attempts, the transcript, the usage, the errors (less the pause's own), the
    call count and Obliterate's check rounds. Raises ``ResumeMismatch`` when the folder is not a pause of THIS job:
    another brief, proposal, sample set, pins, instructions or tools, a folder that is not ``out_dir``, or a record that
    cannot be read."""
    from .contexts import load_context_manifest, plain
    from .conversation import ProtocolError, check_pairing
    folder = Path(folder)
    if folder.resolve() != Path(out_dir).resolve():
        raise ResumeMismatch("a paused method continues in its own folder")
    history_path = folder / "history.json"
    if not history_path.is_file():
        raise ResumeMismatch("the paused writer's folder has no history.json")
    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ResumeMismatch(f"history.json cannot be read: {e}") from e
    if not isinstance(history, dict) or history.get("budget_stop") is not True or history.get("passed"):
        raise ResumeMismatch("the writer did not stop at the spending check")
    transcript = history.get("transcript")
    if (not isinstance(transcript, list) or not transcript or not isinstance(transcript[0], dict)
            or transcript[0].get("content") != first_message):
        raise ResumeMismatch("the writer was briefed for another request, proposal or sample set")
    if transcript[0].get("samples") != [str(f) for f in samples]:
        raise ResumeMismatch("the writer was tried on other sample files")
    context = history.get("context")
    epoch = context.get("epoch") if isinstance(context, dict) else None
    manifest_path = folder / "context" / f"active-{epoch}.json"
    if type(epoch) is not int or not manifest_path.is_file():
        raise ResumeMismatch("the writer's history checkpoint is missing")
    try:
        manifest = load_context_manifest(manifest_path)
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ResumeMismatch(f"the writer's history checkpoint cannot be read: {e}") from e
    if manifest.get("system") != plain(system) or manifest.get("tools") != plain(tools):
        raise ResumeMismatch("the writer had other instructions or tools (another contract or mode)")
    if manifest.get("source_pins") != plain(source_pins):
        raise ResumeMismatch("the sample files, the template or the attachments are not the ones pinned")
    messages = list(manifest.get("messages") or [])
    trimmed = False
    while messages and messages[-1].get("role") != "user":
        messages.pop()                      # the paused call never got its answer: a dangling tool call goes with it
        trimmed = True
    try:
        check_pairing(messages)
    except ProtocolError as e:
        raise ResumeMismatch(f"the saved exchange is not one the model accepts: {e}") from e
    attempts = history.get("attempts")
    if not isinstance(attempts, list) or not all(isinstance(a, dict) and isinstance(a.get("source"), str)
                                                  and isinstance(a.get("problems"), list) for a in attempts):
        raise ResumeMismatch("the recorded attempts cannot be read")
    calls = history.get("calls")
    if type(calls) is not int or calls < 0:
        calls = max((e.get("turn") for e in transcript if isinstance(e, dict) and type(e.get("turn")) is int),
                    default=0)
    errors = [e for e in (history.get("errors") or []) if isinstance(e, str)]
    if errors:
        errors = errors[:-1]                # the pause's own entry: the writer is going on, that call was never made
    check_rounds = history.get("check_rounds")
    seconds = history.get("seconds")
    if trimmed:
        manifest_path.unlink()          # the next checkpoint writes the trimmed exchange (contexts keeps a prefix rule)
    return {"messages": messages, "attempts": attempts, "transcript": [e for e in transcript if isinstance(e, dict)],
            "usage": Usage.from_dict(history.get("usage")), "errors": errors, "context": dict(context),
            "calls": calls, "check_rounds": check_rounds if type(check_rounds) is int and check_rounds >= 0 else 0,
            "seconds": float(seconds) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else 0.0}


def _replay_progress(progress: InvestigationProgress, attempts: Sequence[Dict[str, Any]],
                     transcript: Sequence[Dict[str, Any]]) -> None:
    """The stall guard of a resumed writer, rebuilt from its record in the order things happened: every rejected
    submission counts against its candidate again, and every inspection the tools answered before the pause counts as
    evidence (each one once — the guard cannot tell a re-read from a new read in a capped transcript, so a resumed
    writer is judged on what it submits after the pause)."""
    submissions = iter(attempts)
    names: Dict[Any, str] = {}
    for entry in transcript:
        if entry.get("role") == "assistant":
            for block in entry.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    names[block.get("id")] = str(block.get("name") or "")
        elif entry.get("role") == "user":
            for result in entry.get("tool_results") or []:
                if not isinstance(result, dict):
                    continue
                name = names.get(result.get("tool_use_id"))
                if name == "submit_op":
                    untried = str(result.get("content") or "").startswith("one submission per turn")
                    if result.get("is_error") and not untried:
                        a = next(submissions, None)
                        if a is not None:
                            progress.rejected([_executable_source(a["source"]), a.get("select") or {},
                                               a.get("params") or {}], a["problems"])
                elif name and not result.get("is_error"):
                    progress.observe({"replayed": result.get("tool_use_id")})


def trial_options(g: GeneratedOp) -> Dict[str, Any]:
    """What every later trial of ``g`` (the window's proof, the preview, the run) passes besides its code: the
    governed contract, or the Obliterate flag with its attachments and the failures the writer kept on purpose."""
    if getattr(g, "goal_contract", None) is not None:
        return {"goal_contract": g.goal_contract}
    if getattr(g, "unguarded", False):
        return {"unguarded": True, "attachments": list(getattr(g, "attachments", []) or []),
                "accepted": dict(getattr(g, "accepted_failures", {}) or {})}
    return {}


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
- Builtins: the sandbox has ONLY these names: {builtins}. Any other builtin name (type, getattr, open, OSError, \
MemoryError) raises NameError at run time, and struct, base64 and zipfile cannot be imported: parse bytes with \
slicing and int.from_bytes.
- Define exactly one class deriving from Op with: name, mode = "formatting", description, params_schema, \
select_schema, plan(self, pkg, select, params) -> PlanFragment, apply(self, pkg, frag) -> None. No decorators \
from the engine (no @register) and no describe() — the examples import `prose` and phrase helpers for the sentence \
a built-in op shows in the plan; a one-off transform shows the user's brief instead, so leave those imports out.
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

CODEGEN_READ_TOOLS = ("show_xml", "find_paragraphs", "list_styles", "show_part_text", "read_template",
                      "read_input_file") + tuple(t["name"] for t in INSPECTION_TOOLS)
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


UNGUARDED_CHECK_ROUNDS = 3   # Obliterate: times a method's failed checks go back to the writer before it is used anyway
# Obliterate: the checks a writer may never keep as "part of the change" (``accept_failures``): their failure is a
# member the plan did not predict (V1), a revision the plan did not predict or an id Word cannot read (V4), XML Word
# must repair (V5a, V5c), or a source that moved (V9) — a wrong method, never a change the request asked for. Every
# other check (the text, the structure, the views, the properties, the pages) can be what the request asks for, and
# the user reads the writer's reason beside every copy.
NEVER_KEPT_CHECKS = ("V1", "V4", "V5A", "V5C", "V9")


def _unguarded_tools() -> List[Dict[str, Any]]:
    """The Obliterate writer's tools: the same, plus the user's attachments (``read_template``, ``read_input_file``)
    and ``submit_op``'s ``accept_failures`` — built once, so the tools stay byte-stable."""
    import copy
    from .compiler import TOOLS
    tools = copy.deepcopy(CODEGEN_TOOLS)
    submit = next(t for t in tools if t["name"] == "submit_op")
    submit["input_schema"]["properties"]["accept_failures"] = {
        "type": "array", "description": "Only after a submission came back with failed checks: each failed check "
        "that IS the change the request asks for (a check that cannot describe that change), with the reason the "
        "user will read beside every copy. Never to hide a mistake. " + ", ".join(NEVER_KEPT_CHECKS[:-1])
        + f" and {NEVER_KEPT_CHECKS[-1]} can never be kept: their failure is a member, revision or XML the method "
        "got wrong, or a source that moved — fix the method.",
        "items": {"type": "object", "properties": {"check": {"type": "string", "description": "the check id, e.g. V10"},
                                                    "reason": {"type": "string"}},
                  "required": ["check", "reason"]}}
    return tools[:-1] + [copy.deepcopy(t) for t in TOOLS if t["name"] in ("read_template", "read_input_file")] \
        + [submit]


CODEGEN_TOOLS_UNGUARDED = _unguarded_tools()


def _examples() -> str:
    here = Path(__file__).resolve().parent / "ops"
    out = []
    for name in EXAMPLE_OPS:
        p = here / name
        if p.is_file():
            out.append(f"===== ops/{name} =====\n{p.read_text(encoding='utf-8')}")
    return "\n\n".join(out)


UNGUARDED_LIMITS = (
    "- apply() changes only the working package: pkg.xml / pkg.write_xml for XML, pkg.add_member / "
    "pkg.remove_member / pkg.edit_rels for parts, media and relationships. plan() predicts every text, revision, "
    "member, relationship and content-type change first; apply() reads what plan() put in frag.transform_data "
    "(JSON-serialisable values only).\n"
    "- OBLITERATE MODE: the user lifted the plain-formatting limits for this method. It may change anything Word "
    "stores that the request needs — text in any part, tracked or plain, text boxes, shapes, parts and members — "
    "within the import whitelist and the sandbox. Write only what the request asks for. mode is 'formatting', "
    "'content' (tracked wording changes) or 'untracked' (plain wording changes).\n"
    "- Wording: plan each changed paragraph with the text-edit helpers (plan_paragraph, apply_part_edits; reference "
    "below) and keep their paragraph_edits assertions. Tracked changes carry author=pkg.author; new revision ids "
    "come from RevisionStamp / max_revision_id, above every annotation id in every document part.\n"
    "- Files the user attached (a template, a picture, a table) are pkg.inputs in plan and apply: an immutable "
    "mapping from each attached file's absolute path to its bytes (the first message lists them; DocxPackage(bytes) "
    "opens a .docx). Never open a path.\n"
    "- Assert every change with the independent V10 assertion kinds: {kinds}. An unasserted change fails V10.\n"
    "- The independent checks run on every sample. In this mode a failed check does not stop the method, but it "
    "comes back to you: fix what you did not intend and submit again. When a failure IS the change the request "
    "asks for (a check that cannot describe it), submit again with accept_failures [{{check, reason}}]: the user "
    "reads your reason beside every copy. {never_kept} can never be kept: their failure is a member, revision or "
    "XML the method got wrong, or a source that moved. After {rounds} such rounds the latest method is used with "
    "its failures shown.\n")


def system_prompt(goal_contract=None, unguarded: bool = False) -> str:
    from .catalog import CUSTOM_TRANSFORM_LIMITS
    from .sandbox_runner import SAFE_BUILTINS
    prompt = CODEGEN_SYSTEM.format(allowed=json.dumps({k: sorted(v) for k, v in
                                                     allowed_imports(goal_contract, unguarded).items()}),
                                 plain=sorted(ALLOWED_IMPORT), examples=_examples(),
                                 module_attrs=json.dumps({k: sorted(v) for k, v in MODULE_ATTRS.items()}),
                                 builtins=", ".join(sorted(SAFE_BUILTINS)),
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
        # The plain-formatting allowlist is not the capability boundary of an
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
            "targets from pkg.binding in plan and apply (a target with all:true is bound once, by count: its entry "
            "is {\"targets\": \"all\", \"choice\": \"unique\", \"source_count\": N}, every scoped paragraph of "
            "the document itself). These are fresh job-local copies, not authority you can "
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
    elif unguarded:
        from .verify.props import KINDS
        start, end = prompt.index("- apply() mutates"), prompt.index("- If the request cannot")
        prompt = prompt[:start] + UNGUARDED_LIMITS.format(kinds=json.dumps(sorted(KINDS)),
                                                          rounds=UNGUARDED_CHECK_ROUNDS,
                                                          never_kept=", ".join(NEVER_KEPT_CHECKS)) + prompt[end:]
        prompt = prompt.replace('name, mode = "formatting", description', 'name, mode, description')
        helper = Path(__file__).resolve().parent / "ops" / "textedit.py"
        prompt += "\nPure text-edit planning/application helper reference:\n" + helper.read_text(encoding="utf-8")
    return prompt


def generate_op(request: str, proposal: str, samples: Sequence[Path], out_dir: Path, client=None,
                model: Optional[str] = None, max_iter: int = MAX_ITER, log=None, brief: Optional[str] = None,
                max_calls: int = MAX_CALLS, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
                effort: str = CODEGEN_EFFORT, stall_repetitions: int = DEFAULT_STALL_REPETITIONS,
                max_context_tokens: int = DEFAULT_CONTEXT_TOKENS, analysis_limits=None,
                goal_contract=None, goal_bindings=None, approved_words: Sequence[str] = (),
                unguarded: bool = False, template: Optional[Path] = None,
                input_files: Sequence[Path] = (), resume_from: Optional[Path] = None) -> GeneratedOp:
    """Look → generate → sandbox + verify on the samples → feed failures back: at most ``max_iter`` submissions and
    ``max_calls`` calls. Every tool call is answered in the next message. Existing
    outer budget scopes still apply to every call; local settings cannot bypass them — but the writer thinks at
    ``effort`` (``resources.CODEGEN_EFFORT``) inside them (``llm.effort_scope``), and the calls after it at theirs.
    ``template`` and ``input_files`` (Obliterate only: the files the user attached) are pinned and reach the method
    as ``pkg.inputs``. ``resume_from`` names the folder of a writer the spending check paused (``out_dir`` itself,
    ``GeneratedOp.budget_stop``): the loop goes on from its checkpoint with the same brief, pins, contract and
    approved words, its calls and attempts counting on; a checkpoint that cannot be continued is logged and the
    writer starts afresh (``load_writer_checkpoint``)."""
    from . import llm
    with diag.scope(Path(out_dir) / diag.LOGS / "events.jsonl", codegen_id=diag.new_id()), \
            diag.span("codegen.generate", sample_count=len(samples), max_attempts=max_iter) as record, \
            llm.effort_scope(effort):
        loose = unguarded and goal_contract is None
        result = _generate_op(request, proposal, samples, out_dir, client, model, max_iter, log, brief,
                              max_calls, max_output_tokens, effort, stall_repetitions, max_context_tokens,
                              analysis_limits, goal_contract, goal_bindings, approved_words, loose,
                              template if loose else None, list(input_files) if loose else [], resume_from)
        record.update(passed=result.passed, attempts=len(result.attempts), errors=len(result.errors),
                      budget_stop=result.budget_stop)
        return result


def _generate_op(request, proposal, samples, out_dir, client, model, max_iter, log, brief,
                 max_calls, max_output_tokens, effort, stall_repetitions, max_context_tokens,
                 analysis_limits, goal_contract, goal_bindings, approved_words, unguarded=False, template=None,
                 input_files=(), resume_from=None):
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
    system = system_prompt(goal_contract, unguarded)
    profiles = [profile(DocxPackage.from_path(f)) for f in samples]
    from .goals import goal_templates
    templates = goal_templates(goal_contract) if goal_contract is not None else []
    # Obliterate: the files the user attached, pinned now; every trial of the method gets these bytes or refuses
    attached = [Path(f).resolve() for f in ([template] if template else []) + list(input_files or []) if f]
    attachments = [{"path": str(f), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
                   for f in dict.fromkeys(attached)]
    if template:
        templates = [Path(template).resolve()]
    batch = compiler._Batch(list(samples), templates[0] if templates else None,
                            input_files=[Path(f).resolve() for f in input_files or ()])
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
    attached_note = ("\n\nATTACHED FILES (pkg.inputs keys; read them with read_template, read_input_file, list_parts "
                     "and read_xml_page):\n" + "\n".join(a["path"] for a in attachments)) if attachments else ""
    messages: List[Dict[str, Any]] = [{"role": "user", "content":
        f"{asked}\n\nWHY NO EXISTING OP FITS / PROPOSAL: {proposal}\n\nSAMPLE FILES (for the tools): "
        f"{[batch.label(Path(f)) for f in samples]}\n\nSAMPLE FILE PROFILES:\n"
        + json.dumps(profiles, ensure_ascii=False) + attached_note}]
    tools = CODEGEN_TOOLS_UNGUARDED if unguarded else CODEGEN_TOOLS
    # Full-batch bindings can include deliberate user choices on non-sample files.
    # Keep them, then validate each one afresh when that file actually runs.
    retained_bindings = copy.deepcopy(supplied_bindings)
    retained_bindings.update(checked_bindings)
    g = GeneratedOp(model=model, request=request, proposal=proposal or "", brief=brief or "",
                    goal_contract=goal_contract, goal_bindings=retained_bindings, approved_words=list(approved_words),
                    unguarded=unguarded, attachments=attachments)
    batch.context_state = g.context_state
    t_all = time.perf_counter()
    calls = 0
    check_rounds = 0                          # Obliterate: the rounds of failed checks sent back so far
    resumed = None
    if resume_from is not None:
        try:
            resumed = load_writer_checkpoint(Path(resume_from), out_dir=out_dir, first_message=messages[0]["content"],
                                             samples=samples, system=system, tools=tools, source_pins=batch.source_pins)
        except ResumeMismatch as why:
            log(f"codegen: the paused method cannot be continued ({why}); starting it afresh")
            diag.event("codegen.resume_refused", reason=str(why), folder=str(resume_from))
    if resumed is not None:
        # the writer goes on where the spending check stopped it: its exchange, attempts, usage and counts; the
        # shared context state is updated in place (the batch's tools read the same dict)
        messages = resumed["messages"]
        g.attempts, g.transcript, g.errors, g.usage = (resumed["attempts"], resumed["transcript"], resumed["errors"],
                                                       resumed["usage"])
        g.context_state.clear()
        g.context_state.update(resumed["context"])
        calls, check_rounds = resumed["calls"], resumed["check_rounds"]
        g.calls, g.check_rounds = calls, check_rounds   # (Obliterate's best method so far is not: it is submitted again)
        t_all -= resumed["seconds"]
        _replay_progress(progress, g.attempts, g.transcript)
        g.transcript.append({"turn": calls, "resumed": True, "messages": len(messages)})
        log(f"codegen: continuing the method where it stopped ({calls} call(s) and {len(g.attempts)} attempt(s) "
            "so far; the model keeps what it has looked at)")
        diag.event("codegen.resumed", calls=calls, attempts=len(g.attempts), message_count=len(messages))
    else:
        # a fresh writer in a folder that holds another's checkpoint (a pause that could not be continued) opens a
        # new epoch: the checkpoint keeps an epoch's history as an exact prefix and the old one is left as it was
        used = [int(path.stem[len("active-"):]) for path in (out_dir / "context").glob("active-*.json")
                if path.stem[len("active-"):].isdigit()]
        if used:
            g.context_state["epoch"] = max(used) + 1
        g.transcript.append({"turn": 0, "role": "user", "content": messages[0]["content"],
                             "samples": [str(f) for f in samples]})

    # Obliterate: the best method that ran with only failed checks against it (fewest failures not kept on purpose,
    # then the latest), used when the writer stops without one that passes or keeps every failure with a reason
    candidate: Dict[str, Any] = {}

    def adopt(c) -> None:
        g.source, g.select, g.params, g.passed, g.trials = c["source"], c["select"], c["params"], True, c["trials"]
        g.accepted_failures = dict(c["accepted"])
        g.name = _op_name(g.source)
        g.path = save_generated(g, out_dir)

    def done(budget_stop: bool = False) -> GeneratedOp:   # every way out keeps codegen/history.json
        from .contexts import checkpoint_history
        if unguarded and not g.passed and candidate and not budget_stop:
            # the checks warn in Obliterate mode: the writer stopped, so the best method that ran is shown with them
            log(f"codegen: using attempt {candidate['attempt']}, which ran with failed checks the user will see")
            adopt(candidate)
        checkpoint_history(messages, **context_arguments())
        g.budget_stop, g.calls, g.check_rounds = budget_stop, calls, check_rounds
        g.seconds = time.perf_counter() - t_all
        _save_history(g, out_dir)
        return g

    def context_arguments():
        return {"system": system, "tools": tools, "folder": out_dir, "state": g.context_state,
                "authority": {"request": request, "files": batch.labels, "goal_contract": goal_contract,
                              "goal_bindings": checked_bindings, "approved_word_sources": list(approved_words),
                              "proposal_diagnostic_only": proposal}, "source_pins": batch.source_pins}

    def client_now():
        nonlocal client
        if client is None:
            client = compiler._client()
        return client
    while len(g.attempts) < max_iter and calls < max_calls:
        from .contexts import ContextLimit, manage_context
        try:
            messages = manage_context(messages, max_tokens=max_context_tokens, **context_arguments())
        except ContextLimit as error:
            g.errors.append(str(error))
            return done()
        calls += 1
        g.calls = calls
        t_call = time.perf_counter()
        try:
            resp = llm.call(client_now(), model=model, system=system, tools=tools, messages=messages,
                            max_output_tokens=policy.max_output_tokens, effort=policy.effort)
        except Exception as e:  # noqa: BLE001 — classified below
            if not llm.is_api_error(e) and not isinstance(e, llm.BudgetExceeded):
                raise                          # a defect (a wrong argument, a test's script running out) propagates
            g.errors.append(llm.failure_text(e, "the call to the model failed"))
            if isinstance(e, llm.BudgetExceeded):
                # a pause for the user's go-ahead is theirs to answer, never a reason to use a method on its own; the
                # transcript records a pause, not a failed call (none was made: a writer continued from here makes
                # this call as its next, so it is not counted)
                g.transcript.append({"turn": calls, "paused": str(e),
                                     "seconds": round(time.perf_counter() - t_call, 2)})
                calls -= 1
                g.calls = calls
                return done(budget_stop=True)
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
                accepted = _accepted_failures(inp.get("accept_failures")) if unguarded else {}
                refused_keeps = _never_kept(inp.get("accept_failures")) if unguarded else []
                checks: List[str] = []                 # Obliterate: failed checks not kept on purpose (warnings)
                with diag.scope(codegen_attempt=attempt), \
                        diag.span("codegen.attempt", model_turn=calls, tool_use_id=b.id) as record:
                    problems = check_source(src, goal_contract=goal_contract, unguarded=unguarded)
                    diag.event("codegen.source_checked", accepted=not bool(problems), problem_count=len(problems))
                    trials: List[FileTrial] = []
                    if not problems:
                        for i, f in enumerate(samples):
                            t = trial(src, Path(f), select, params, out_dir / "sandbox" / f"attempt{attempt}" / f"{i:02d}",
                                      goal_contract=goal_contract, goal_binding=checked_bindings.get(str(Path(f).resolve())),
                                      unguarded=unguarded, attachments=attachments, accepted=accepted)
                            trials.append(t)
                            problems += [f"{Path(f).name}: {p}" for p in t.problems]
                            checks += [f"{Path(f).name}: {line}" for line in _unkept_failures(t, accepted)]
                    record.update(passed=not bool(problems), problem_count=len(problems), trial_count=len(trials),
                                  failed_checks=len(checks))
                g.attempts.append({"attempt": attempt, "problems": problems + checks,
                                   "trials": [t.to_dict() for t in trials], "source": src, "select": select,
                                   "params": params, **({"accept_failures": accepted} if accepted else {})})
                log(f"codegen attempt {attempt}: "
                    + ('PASS' if not problems and not checks else
                       f'{len(problems)} problem(s)' if problems else f'ran; {len(checks)} failed check(s)'))
                if not problems and checks and check_rounds < UNGUARDED_CHECK_ROUNDS:
                    # Obliterate: it ran, but checks failed that the writer did not keep with a reason — they go
                    # back to it once more, and the best such method so far is kept in case it stops here
                    check_rounds += 1
                    g.check_rounds = check_rounds
                    if not candidate or len(checks) <= candidate["failures"]:
                        candidate.clear()
                        candidate.update(source=src, select=select, params=params, trials=trials, accepted=accepted,
                                         failures=len(checks), attempt=attempt)
                    results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True, "content": (
                        f"RAN, BUT {len(checks)} CHECK(S) FAILED (round {check_rounds} of {UNGUARDED_CHECK_ROUNDS}; "
                        "Obliterate mode: failed checks do not stop the method, the user reads them beside each "
                        "copy). Fix what you did not intend and submit again; if a failure IS the change the "
                        "request asks for, submit again with accept_failures [{check, reason}]."
                        + (f" accept_failures cannot keep {', '.join(refused_keeps)}: that failure is a member, "
                           "revision or XML the method got wrong, or a source that moved — fix the method."
                           if refused_keeps else "")
                        + "\n- " + "\n- ".join(checks))[:12000]})
                    stalled = progress.rejected([_executable_source(src), select, params], checks)
                    continue
                if not problems:
                    g.source, g.select, g.params, g.passed, g.trials = src, select, params, True, trials
                    g.accepted_failures = dict(accepted)
                    g.name = _op_name(src)
                    g.path = save_generated(g, out_dir)
                    return done()                  # after the result is set: the history says it passed
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                "content": ("FAILED — fix and submit again:\n- " + "\n- ".join(problems))[:12000]})
                stalled = progress.rejected([_executable_source(src), select, params], problems)
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


def _accepted_failures(raw: Any) -> Dict[str, str]:
    """``submit_op``'s ``accept_failures`` (Obliterate): check id → the writer's reason; entries without both are
    dropped, so an unexplained failure is never kept on purpose, and so are the checks no reason can keep
    (``NEVER_KEPT_CHECKS``: ``_never_kept`` names them for the writer)."""
    out: Dict[str, str] = {}
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            check, reason = str(item.get("check") or "").strip().upper(), " ".join(str(item.get("reason") or "").split())
            if check and reason and check not in NEVER_KEPT_CHECKS:
                out[check] = reason[:600]
    return out


def _never_kept(raw: Any) -> List[str]:
    """The checks a submission asked to keep that no reason can keep (``NEVER_KEPT_CHECKS``), as the writer wrote them."""
    out: List[str] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            check = str(item.get("check") or "").strip().upper()
            if check in NEVER_KEPT_CHECKS and check not in out:
                out.append(check)
    return out


def _unkept_failures(t: FileTrial, accepted: Dict[str, str]) -> List[str]:
    """The checks an Obliterate trial failed that the writer did not keep with a reason, each with its summary."""
    if not t.passed or not t.unverified or t.record is None:
        return []
    return [f"{c.id} FAIL: {c.summary[:600]}" for c in t.record.checks
            if c.status == "FAIL" and c.id.upper() not in accepted]


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
                                                "goal_contract": g.goal_contract, "goal_bindings": g.goal_bindings,
                                                # the pause at the spending check, and what a resumed writer counts on from
                                                "budget_stop": g.budget_stop, "calls": g.calls,
                                                "check_rounds": g.check_rounds})


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
                                                     "unguarded": g.unguarded, "attachments": g.attachments,
                                                     "accepted_failures": g.accepted_failures,
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
                    options = trial_options(g)
                    t = trial(g.source, f, g.select, g.params, Path(tmp) / f"{i:04d}",
                              goal_binding=g.goal_bindings.get(key), **options)
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
                        evidence = f"{Path(name).stem}.record.json"
                        (rd / evidence).write_text(json.dumps(t.record.to_dict(), indent=2, ensure_ascii=False),
                                                   encoding="utf-8")
                        (rd / f"{Path(name).stem}.record.md").write_bytes(t.record.to_markdown(evidence).encode("utf-8"))
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
        metadata.update(goal_contract=copy.deepcopy(g.goal_contract), goal_bindings=copy.deepcopy(g.goal_bindings),
                        enforce_goals=True)
    # The companion prevents a damaged set-aside manifest from dropping outcome authority.
    atomic_json(out_dir / "generated.json", {**metadata, "results": [result.to_dict() for result in results]})
    atomic_json(out_dir / "set_aside.json", metadata)
