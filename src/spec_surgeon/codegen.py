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
   to the model with the record; at most ``max_iter`` (3) attempts.

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

from . import diagnostics as diag
from .llm import Usage

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
                                   "size_applies", "size_values"},
    "lxml": {"etree"},
    "__future__": {"annotations"},
    "typing": {"Any", "Dict", "List", "Optional", "Tuple", "Sequence", "Set", "Iterable"},
    "dataclasses": {"dataclass", "field"},
    "collections": {"Counter", "OrderedDict", "defaultdict"},
}
ALLOWED_IMPORT = {"re", "copy", "math", "itertools", "functools", "json"}
# What the reference ops import that a generated op may NOT: the tracked-formatting helpers (generated ops are plain
# formatting — no revision of any kind) and the engine's registration hook. tests/test_codegen.py pins that every name
# the examples import is either allowed above or listed here.
EXCLUDED_FROM_EXAMPLES: Dict[str, set] = {
    "spec_surgeon.ooxml": {"RevisionStamp", "property_change", "max_revision_id", "file_uses_date_utc"},
    "spec_surgeon.ops.replace_text": {"default_author"},
    "spec_surgeon.ops": {"register"},
}
EXAMPLE_OPS = ("set_page_setup.py", "set_paragraph_props.py", "set_font.py")
MAX_CALLS = 24
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
MAX_ITER = 3


def check_source(src: str) -> List[str]:
    """Whitelist problems of a generated op's source ([] = acceptable)."""
    try:
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
            if node.level or node.module not in ALLOWED_FROM:
                probs.append(f"line {node.lineno}: 'from {'.' * node.level}{node.module} import …' is not allowed")
            else:
                bad = [a.name for a in node.names if a.name not in ALLOWED_FROM[node.module]]
                if bad:
                    probs.append(f"line {node.lineno}: from {node.module} import {bad} is not allowed "
                                 f"(allowed: {sorted(ALLOWED_FROM[node.module])})")
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
                   timeout: int = SANDBOX_TIMEOUT) -> SandboxResult:
    """plan + apply of the generated op on a COPY of ``docx`` in a separate isolated process."""
    with diag.scope(sandbox_id=diag.new_id()), \
            diag.span("codegen.sandbox", source=str(docx), workdir=str(workdir), timeout_seconds=timeout) as record:
        result = _run_in_sandbox(source, docx, select, params, workdir, timeout)
        record.update(ok=result.ok, code=result.code, output=str(result.output) if result.output else None)
        return result


def _run_in_sandbox(source, docx, select, params, workdir, timeout):
    t0 = time.perf_counter()
    workdir = Path(workdir).resolve()          # the sandbox runs with cwd=workdir: every path it gets is absolute
    workdir.mkdir(parents=True, exist_ok=True)
    copy_path = workdir / "source.docx"
    shutil.copyfile(Path(docx).resolve(), copy_path)
    req = workdir / "request.json"
    req.write_text(json.dumps({"source": source, "docx": str(copy_path), "select": select, "params": params}),
                   encoding="utf-8")
    diag.event("codegen.sandbox.prepared", source_copy=str(copy_path), request_path=str(req))
    env = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONIOENCODING")}
    try:
        with diag.span("codegen.sandbox.process", executable=sys.executable, timeout_seconds=timeout) as record:
            proc = subprocess.run([sys.executable, "-I", "-m", "spec_surgeon.sandbox_runner", str(req)], cwd=str(workdir),
                                  env=env, timeout=timeout, capture_output=True, check=False)
            record["returncode"] = proc.returncode
    except subprocess.TimeoutExpired as e:
        diag.event("codegen.sandbox.timeout", level="warning", timeout_seconds=timeout)
        err = (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else str(e.stderr or "")
        return SandboxResult(False, error=f"timed out after {timeout}s", code="timeout",
                             seconds=time.perf_counter() - t0, stderr=_tail(err))
    stderr = (proc.stderr or b"").decode("utf-8", "replace") if isinstance(proc.stderr, bytes) else str(proc.stderr or "")
    (workdir / "sandbox-stderr.txt").write_text(stderr, encoding="utf-8")
    res_path = workdir / "result.json"
    diag.event("codegen.sandbox.exited", returncode=proc.returncode, result_present=res_path.is_file(),
               stderr_characters=len(stderr), stderr_path=str(workdir / "sandbox-stderr.txt"))
    if not res_path.is_file():
        diag.event("codegen.sandbox.result_missing", level="error", returncode=proc.returncode)
        return SandboxResult(False, error=f"the sandbox process died without a result (exit code {proc.returncode})"
                             + (f"; it printed: {_tail(stderr, 6)}" if stderr.strip() else ""), code="crash",
                             seconds=time.perf_counter() - t0, stderr=_tail(stderr, 40), returncode=proc.returncode)
    res = json.loads(res_path.read_text(encoding="utf-8"))
    if not res.get("ok"):
        return SandboxResult(False, error=res.get("error"), code=res.get("code"), seconds=time.perf_counter() - t0,
                             traceback=res.get("traceback") or "", stderr=_tail(stderr, 40),
                             returncode=proc.returncode)
    return SandboxResult(True, output=workdir / "out.docx",
                         plan=json.loads((workdir / "plan.json").read_text(encoding="utf-8")),
                         seconds=time.perf_counter() - t0)


def hard_limits(plan: Dict[str, Any], allowed_parts: Optional[Sequence[str]] = None) -> List[str]:
    """What a generated op may never do, judged from its own plan before the suite runs. ``allowed_parts``: the source's
    text parts and its styles part — the parts V10's residual identity covers; any other part (numbering.xml, docProps,
    a chart…) could carry a change no verified assertion checks, so it is refused outright."""
    probs = []
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


def allowed_parts_of(docx: Path) -> List[str]:
    """The parts a generated op may change in this file: its text parts and its styles part."""
    from .ops.selectors import StyleSheet
    from .package import DocxPackage
    pkg = DocxPackage.from_path(docx)
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

    def to_dict(self):
        return {"source": str(self.source), "passed": self.passed, "problems": self.problems,
                "output": str(self.output) if self.output else None, "output_md5": self.output_md5}


def trial(source: str, f: Path, select, params, workdir: Path) -> FileTrial:
    with diag.scope(trial_id=diag.new_id()), \
            diag.span("codegen.trial", source=str(f), workdir=str(workdir)) as record:
        result = _trial(source, f, select, params, workdir)
        record.update(passed=result.passed, problem_count=len(result.problems), output_md5=result.output_md5,
                      failed_checks=[c.id for c in result.record.checks if c.status == "FAIL"]
                      if result.record is not None else [])
        return result


def _trial(source, f, select, params, workdir):
    from .verify import run_suite
    sb = run_in_sandbox(source, f, select, params, workdir)
    if not sb.ok:
        where = _tail(sb.traceback, 12)
        return FileTrial(f, False, [f"[{sb.code or 'error'}] {sb.error}"
                                    + (f"\n(traceback, last lines:)\n{where}" if where else "")])
    try:
        with diag.span("codegen.trial.limits") as record:
            limits = hard_limits(sb.plan, allowed_parts_of(f))
            record.update(accepted=not bool(limits), problem_count=len(limits))
    except Exception as e:  # noqa: BLE001 — a plan the engine cannot even read is a failed trial, with the reason
        return FileTrial(f, False, [f"the generated op's plan is malformed ({type(e).__name__}: {e})"], sb.output)
    if limits:
        return FileTrial(f, False, limits, sb.output)
    out_bytes = sb.output.read_bytes()
    try:
        with diag.span("codegen.trial.verify") as outcome:
            rec = run_suite(Path(f).read_bytes(), out_bytes, sb.plan, str(f), str(sb.output))
            outcome.update(passed=rec.passed, checks={c.id: c.status for c in rec.checks})
    except Exception as e:  # noqa: BLE001 — an output the suite cannot read (a part removed, a broken zip): FAIL
        return FileTrial(f, False, [f"the verifier could not check the output at all ({type(e).__name__}: {e}); "
                                    f"the generated op produced a package the suite cannot read — a removed or "
                                    f"broken part?"], sb.output)
    fails = [f"{c.id} FAIL: {c.summary[:600]}" for c in rec.checks if c.status == "FAIL"]
    return FileTrial(f, rec.passed, fails, sb.output, rec, hashlib.md5(out_bytes).hexdigest())


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

CODEGEN_READ_TOOLS = ("show_xml", "find_paragraphs", "list_styles", "show_part_text")
CODEGEN_TOOLS = [
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


def system_prompt() -> str:
    from .catalog import CUSTOM_TRANSFORM_LIMITS
    return CODEGEN_SYSTEM.format(allowed=json.dumps({k: sorted(v) for k, v in ALLOWED_FROM.items()}),
                                 plain=sorted(ALLOWED_IMPORT), examples=_examples(),
                                 module_attrs=json.dumps({k: sorted(v) for k, v in MODULE_ATTRS.items()}),
                                 limits=json.dumps(CUSTOM_TRANSFORM_LIMITS, ensure_ascii=False))


def generate_op(request: str, proposal: str, samples: Sequence[Path], out_dir: Path, client=None,
                model: Optional[str] = None, max_iter: int = MAX_ITER, log=None, brief: Optional[str] = None) -> GeneratedOp:
    """Look → generate → sandbox + verify on the samples → feed failures back: at most ``max_iter`` submissions and
    ``MAX_CALLS`` calls. Every tool call is answered in the next message."""
    with diag.scope(Path(out_dir) / diag.LOGS / "events.jsonl", codegen_id=diag.new_id()), \
            diag.span("codegen.generate", sample_count=len(samples), max_attempts=max_iter) as record:
        result = _generate_op(request, proposal, samples, out_dir, client, model, max_iter, log, brief)
        record.update(passed=result.passed, attempts=len(result.attempts), errors=len(result.errors))
        return result


def _generate_op(request, proposal, samples, out_dir, client, model, max_iter, log, brief):
    from . import compiler, llm
    from .profile import profile
    from .package import DocxPackage
    log = log or (lambda m: None)
    model = model or compiler.DEFAULT_MODEL
    out_dir = Path(out_dir)
    system = system_prompt()
    profiles = [profile(DocxPackage.from_path(f)) for f in samples]
    batch = compiler._Batch(list(samples), None)
    asked = (f"THE CONVERSATION WITH THE USER (what they asked, what was understood, every reply):\n{brief}"
             if brief else f"REQUEST: {request}")
    messages: List[Dict[str, Any]] = [{"role": "user", "content":
        f"{asked}\n\nWHY NO EXISTING OP FITS / PROPOSAL: {proposal}\n\nSAMPLE FILES (for the tools): "
        f"{[batch.label(Path(f)) for f in samples]}\n\nSAMPLE FILE PROFILES:\n"
        + json.dumps(profiles, ensure_ascii=False)}]
    g = GeneratedOp(model=model, request=request, proposal=proposal or "", brief=brief or "")
    g.transcript.append({"turn": 0, "role": "user", "content": messages[0]["content"],
                         "samples": [str(f) for f in samples]})
    t_all = time.perf_counter()

    def done() -> GeneratedOp:                     # every way out keeps codegen/history.json
        g.seconds = time.perf_counter() - t_all
        _save_history(g, out_dir)
        return g

    def client_now():
        nonlocal client
        if client is None:
            client = compiler._client()
        return client
    calls = 0
    while len(g.attempts) < max_iter and calls < MAX_CALLS:
        calls += 1
        t_call = time.perf_counter()
        try:
            resp = llm.call(client_now(), model=model, system=system, tools=CODEGEN_TOOLS, messages=messages)
        except Exception as e:  # noqa: BLE001 — classified below
            if not llm.is_api_error(e):
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
                    problems = check_source(src)
                    diag.event("codegen.source_checked", accepted=not bool(problems), problem_count=len(problems))
                    trials: List[FileTrial] = []
                    if not problems:
                        for i, f in enumerate(samples):
                            t = trial(src, Path(f), select, params, out_dir / "sandbox" / f"attempt{attempt}" / f"{i:02d}")
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
                continue
            try:
                if b.name not in CODEGEN_READ_TOOLS:
                    raise KeyError(f"unknown tool {b.name}")
                out = getattr(batch, b.name)(**inp)
                results.append({"type": "tool_result", "tool_use_id": b.id,
                                "content": json.dumps(out, ensure_ascii=False)[:30000]})
            except Exception as e:  # noqa: BLE001 — every tool call gets an answer, or the next request is refused
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                "content": f"{type(e).__name__}: {e}"[:2000]})
        messages.append({"role": "user", "content": results})
        g.transcript.append(llm.results_entry(calls, results))
        g.seconds = time.perf_counter() - t_all
        _save_history(g, out_dir)                  # every attempt kept as it happens
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
                                                "transcript": g.transcript})


def pick_samples(files: Sequence[Path]) -> List[Path]:
    """The files a generated op is proven on: the first file of each structure pattern, up to three — by path (two
    files may share a name in different folders)."""
    from .profile import profile_batch
    files = [Path(f) for f in files]
    out: List[Path] = []
    for pat in profile_batch(files)["patterns"][:3]:
        paths = pat.get("paths") or []
        out.append(Path(paths[0]) if paths else next(f for f in files if f.name == pat["files"][0]))
    return out


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
                                                     "model": g.model, "usage": g.usage.to_dict(),
                                                     "attempts": g.attempts}, indent=2, default=str), encoding="utf-8")
    return p


def run_generated(g: GeneratedOp, inputs: Sequence[str], out_dir: Path, log=None) -> List[FileTrial]:
    """Apply a passing generated op to every input (sandbox per file, verifier per file). Verified outputs are copied
    to ``out_dir/outputs`` under names unique across the batch, and only when their bytes are the ones the suite
    verified; records to ``out_dir/records``. Never in place: an input inside the results folder is refused before
    anything is written (the same rules as a batch run)."""
    from .batch import check_inputs_outside, expand_inputs, output_names
    log = log or (lambda m: None)
    out_dir = Path(out_dir)
    files = expand_inputs(inputs)
    check_inputs_outside(files, out_dir)
    with diag.scope(out_dir / diag.LOGS / "events.jsonl", generated_run_id=diag.new_id()), \
            diag.span("codegen.run", file_count=len(files)) as record:
        result = _run_generated_files(g, files, out_dir, log, output_names)
        record.update(passed=sum(t.passed for t in result), failed=sum(not t.passed for t in result))
        return result


def _run_generated_files(g, files, out_dir, log, output_names):
    names = output_names(files)
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, f in enumerate(files):
            with diag.scope(source=str(f)), diag.span("codegen.file") as outcome:
                t = trial(g.source, f, g.select, g.params, Path(tmp) / f"{i:04d}")
                name = names[str(f)]
                if t.passed:
                    data = t.output.read_bytes()
                    actual_md5 = hashlib.md5(data).hexdigest()
                    diag.event("codegen.delivery.identity", expected_md5=t.output_md5, actual_md5=actual_md5,
                               matches=actual_md5 == t.output_md5)
                    if actual_md5 != t.output_md5:
                        t.passed, t.output = False, None
                        t.problems.append("the output is not the one the suite verified; not delivered")
                    else:
                        dest = out_dir / "outputs" / name
                        with diag.span("codegen.delivery.write", output=str(dest), size=len(data)):
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(data)
                            t.output = dest
                else:
                    t.output = None
                if t.record is not None:
                    rd = out_dir / "records"
                    with diag.span("codegen.file.records", out_dir=str(rd)):
                        rd.mkdir(parents=True, exist_ok=True)
                        (rd / f"{Path(name).stem}.record.md").write_text(t.record.to_markdown(), encoding="utf-8")
                results.append(t)
                log(f"{'PASS' if t.passed else 'FAIL'} {f.name}")
                outcome.update(passed=t.passed, problem_count=len(t.problems),
                               output=str(t.output) if t.output else None, output_md5=t.output_md5)
    return results
