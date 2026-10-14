"""Tier B — a generated transform for a request no op can express (docs/NL_SURGICAL_EDITOR_PLAN.md §8; Phase 5).

Off by default (decision on §14.3: the user turns it on with ``--allow-codegen``). When the compiler answers
``unsupported`` with a ``proposal``, ``generate_op`` asks Claude to write ONE op module against a restricted API
(the ``Op`` interface, ``DocxPackage``, ``ooxml`` helpers, selectors, lxml) and tests it:

1. ``check_source`` — an AST whitelist: imports only from ``ALLOWED_FROM`` (named symbols) and a few pure stdlib
   modules; no dunder attributes (bar ``__init__``), no ``open``/``exec``/``eval``/``getattr``/``__import__``…,
   no file-writing or file-reading attributes; exactly one ``Op`` subclass with ``plan`` and ``apply``;
2. ``run_in_sandbox`` — the module runs in a separate isolated process (``python -I``, temp working directory,
   stripped environment, timeout) on a COPY of each sample file: plan (predict first), then apply;
3. ``hard_limits`` — the plan may not change text in surviving parts (generated ops are formatting-only in this
   prototype), add or drop tracked insertions/deletions, touch ``settings.xml``, or claim no layout reference
   without a reason;
4. the **full verification suite** (independent verifier, V1–V5, V8, V10) on every sample. A failure goes back
   to the model with the record; at most ``max_iter`` (3) attempts.

A passing op is saved (``generated_ops/<hash>.py`` + ``.history.json`` beside the batch's records) — a cache, not
a registered op: promotion into ``ops/`` is a human decision, with tests. ``run_generated`` applies it to a batch
the same way (sandbox per file, verifier per file); outputs go to the output folder only — never in place.
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

ALLOWED_FROM: Dict[str, set] = {
    "spec_surgeon.ops.base": {"Op", "PlanFragment", "BlockDiff", "JobException"},
    "spec_surgeon.package": {"W", "NS", "R", "DocxPackage", "serialize_xml", "parse_xml"},
    "spec_surgeon.ooxml": {"PPR_ORDER", "RPR_ORDER", "SECTPR_ORDER", "STYLE_ORDER", "insert_in_order", "get_or_add",
                           "paragraphs", "paragraph_ppr", "run_rpr", "mark_rpr", "set_rfonts", "block_full_text",
                           "count_revisions"},
    "spec_surgeon.ops.selectors": {"resolve_sections", "scope_parts", "paragraph_selected", "check_paragraph_selector",
                                   "is_targeted", "StyleSheet", "DEFAULT_PRESERVE_FONTS"},
    "lxml": {"etree"},
    "__future__": {"annotations"},
    "typing": {"Any", "Dict", "List", "Optional", "Tuple", "Sequence", "Set", "Iterable"},
    "dataclasses": {"dataclass", "field"},
    "collections": {"Counter", "OrderedDict", "defaultdict"},
}
ALLOWED_IMPORT = {"re", "copy", "math", "itertools", "functools", "json"}
ALLOWED_MODULES = tuple(sorted(set(ALLOWED_FROM) | ALLOWED_IMPORT))
FORBIDDEN_NAMES = {"open", "exec", "eval", "compile", "__import__", "globals", "locals", "vars", "getattr", "setattr",
                   "delattr", "input", "breakpoint", "memoryview", "help", "exit", "quit", "type", "object"}
FORBIDDEN_ATTRS = {"from_path", "write_bytes", "write_text", "read_bytes", "read_text", "unlink", "rmdir", "mkdir",
                   "system", "popen", "spawn", "fork", "parse", "iterparse", "XMLParser", "write", "repack",
                   "output_bytes", "copy_tree", "rename", "replace_file", "chmod"}
SANDBOX_TIMEOUT = 60
MAX_ITER = 3


def check_source(src: str) -> List[str]:
    """Whitelist problems of a generated op's source ([] = acceptable)."""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"syntax error: {e}"]
    probs: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name not in ALLOWED_IMPORT:
                    probs.append(f"line {node.lineno}: 'import {a.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in ALLOWED_FROM:
                probs.append(f"line {node.lineno}: 'from {'.' * node.level}{node.module} import …' is not allowed")
            else:
                bad = [a.name for a in node.names if a.name not in ALLOWED_FROM[node.module]]
                if bad:
                    probs.append(f"line {node.lineno}: from {node.module} import {bad} is not allowed "
                                 f"(allowed: {sorted(ALLOWED_FROM[node.module])})")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            probs.append(f"line {node.lineno}: '{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr != "__init__":
                probs.append(f"line {node.lineno}: dunder attribute '{node.attr}' is not allowed")
            elif node.attr in FORBIDDEN_ATTRS:
                probs.append(f"line {node.lineno}: attribute '{node.attr}' is not allowed (use pkg.xml / "
                             f"pkg.write_xml on the working package)")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            probs.append(f"line {node.lineno}: global/nonlocal is not allowed")
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


def run_in_sandbox(source: str, docx: Path, select: Dict[str, Any], params: Dict[str, Any], workdir: Path,
                   timeout: int = SANDBOX_TIMEOUT) -> SandboxResult:
    """plan + apply of the generated op on a COPY of ``docx`` in a separate isolated process."""
    t0 = time.perf_counter()
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    copy_path = workdir / "source.docx"
    shutil.copyfile(docx, copy_path)
    req = workdir / "request.json"
    req.write_text(json.dumps({"source": source, "docx": str(copy_path), "select": select, "params": params}),
                   encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k.upper() in ("PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONIOENCODING")}
    try:
        subprocess.run([sys.executable, "-I", "-m", "spec_surgeon.sandbox_runner", str(req)], cwd=str(workdir),
                       env=env, timeout=timeout, capture_output=True, check=False)
    except subprocess.TimeoutExpired:
        return SandboxResult(False, error=f"timed out after {timeout}s", code="timeout",
                             seconds=time.perf_counter() - t0)
    res_path = workdir / "result.json"
    if not res_path.is_file():
        return SandboxResult(False, error="the sandbox process died without a result", code="crash",
                             seconds=time.perf_counter() - t0)
    res = json.loads(res_path.read_text(encoding="utf-8"))
    if not res.get("ok"):
        return SandboxResult(False, error=res.get("error"), code=res.get("code"), seconds=time.perf_counter() - t0)
    return SandboxResult(True, output=workdir / "out.docx",
                         plan=json.loads((workdir / "plan.json").read_text(encoding="utf-8")),
                         seconds=time.perf_counter() - t0)


def hard_limits(plan: Dict[str, Any]) -> List[str]:
    probs = []
    removed, added = set(plan.get("members_removed") or []), set(plan.get("members_added") or [])
    kept = [d for d in plan.get("diffs") or [] if d["part"] not in removed and d["part"] not in added]
    if kept:
        probs.append(f"generated ops may not change text in surviving parts ({len(kept)} block(s) predicted)")
    if plan.get("members_added"):
        probs.append("generated ops may not add package members")
    for k in ("ins", "del", "moveFrom", "moveTo", "ins_mark", "del_mark"):
        if (plan.get("rev_delta") or {}).get(k):
            probs.append(f"generated ops may not add or drop tracked insertions/deletions ({k})")
    if any(p.endswith("settings.xml") for p in plan.get("intended_parts") or []):
        probs.append("generated ops may not edit settings.xml")
    if plan.get("geometry_reference") == "none" and not (plan.get("geometry_reason") or "").strip():
        probs.append("geometry_reference 'none' needs a geometry_reason")
    return probs


@dataclass
class FileTrial:
    source: Path
    passed: bool
    problems: List[str] = field(default_factory=list)
    output: Optional[Path] = None
    record: Optional[Any] = None

    def to_dict(self):
        return {"source": str(self.source), "passed": self.passed, "problems": self.problems,
                "output": str(self.output) if self.output else None}


def trial(source: str, f: Path, select, params, workdir: Path) -> FileTrial:
    from .verify import run_suite
    sb = run_in_sandbox(source, f, select, params, workdir)
    if not sb.ok:
        return FileTrial(f, False, [f"[{sb.code or 'error'}] {sb.error}"])
    limits = hard_limits(sb.plan)
    if limits:
        return FileTrial(f, False, limits, sb.output)
    rec = run_suite(Path(f).read_bytes(), sb.output.read_bytes(), sb.plan, str(f), str(sb.output))
    fails = [f"{c.id} FAIL: {c.summary[:600]}" for c in rec.checks if c.status == "FAIL"]
    return FileTrial(f, rec.passed, fails, sb.output, rec)


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


CODEGEN_SYSTEM = """You write ONE Python module: an operation (an `Op` subclass) for a deterministic .docx editing \
engine, for a request its operation library cannot express. Your module is checked by a whitelist, executed in a \
sandbox on copies of sample files, and judged by an independent verification suite. You never see the verifier's \
code; you see its record when a check fails.

Rules:
- Imports: only `from <module> import <names>` from this whitelist: {allowed}; plus `import` of {plain}. No other \
imports, no file or network access, no open/exec/eval/getattr, no dunder attributes except __init__.
- Define exactly one class deriving from Op with: name, mode = "formatting", description, params_schema, \
select_schema, plan(self, pkg, select, params) -> PlanFragment, apply(self, pkg, frag) -> None. No decorators \
from the engine (no @register).
- plan() must PREDICT everything from the source package analytically, before apply() runs: intended_parts \
(every part whose bytes change), property_assertions (one per changed property family; V10 compares everything \
else in changed parts and styles.xml for identity, so an unasserted change FAILS), touches, notes, \
geometry_reference/geometry_reason. Use self.fragment(select, params) to start the fragment.
- apply() mutates the working package only through pkg.xml(part) / pkg.write_xml(part, root); it reads what plan() \
put in frag.transform_data (JSON-serialisable values only).
- Formatting only: never add, remove or change document text; never touch tracked changes or settings.xml.
- If the request cannot be done safely on a file, raise JobException(reason, code) instead of guessing.

Reference ops from the library (relative imports there become the absolute whitelist imports here):
{examples}

Finish by calling submit_op with the complete module source, the selector and the params to run it with."""

CODEGEN_TOOLS = [
    {"name": "submit_op", "description": "Submit the op module source with the select/params to run it with.",
     "input_schema": {"type": "object", "properties": {
         "source": {"type": "string"}, "select": {"type": "object"}, "params": {"type": "object"},
         "notes": {"type": "string"}}, "required": ["source"]}},
]


def _examples() -> str:
    here = Path(__file__).resolve().parent / "ops"
    out = []
    for name in ("set_page_setup.py", "set_paragraph_props.py"):
        p = here / name
        if p.is_file():
            out.append(f"===== ops/{name} =====\n{p.read_text(encoding='utf-8')}")
    return "\n\n".join(out)


def generate_op(request: str, proposal: str, samples: Sequence[Path], out_dir: Path, client=None,
                model: Optional[str] = None, max_iter: int = MAX_ITER, log=None) -> GeneratedOp:
    """Generate → sandbox + verify on the samples → feed failures back, up to ``max_iter`` attempts."""
    from .compiler import DEFAULT_MODEL, FALLBACK_BETA, _client
    from .profile import profile
    from .package import DocxPackage
    log = log or (lambda m: None)
    client = client or _client()
    model = model or DEFAULT_MODEL
    out_dir = Path(out_dir)
    system = CODEGEN_SYSTEM.format(allowed=json.dumps({k: sorted(v) for k, v in ALLOWED_FROM.items()}),
                                   plain=sorted(ALLOWED_IMPORT), examples=_examples())
    profiles = [profile(DocxPackage.from_path(f)) for f in samples]
    messages: List[Dict[str, Any]] = [{"role": "user", "content":
        f"REQUEST: {request}\n\nWHY NO EXISTING OP FITS / PROPOSAL: {proposal}\n\nSAMPLE FILE PROFILES:\n"
        + json.dumps(profiles, ensure_ascii=False)}]
    g = GeneratedOp()
    for attempt in range(1, max_iter + 1):
        resp = client.beta.messages.create(model=model, max_tokens=16000, system=system, tools=CODEGEN_TOOLS,
                                           messages=messages, output_config={"effort": "high"},
                                           cache_control={"type": "ephemeral"}, betas=[FALLBACK_BETA],
                                           fallbacks="default")
        if getattr(resp, "stop_reason", None) in ("refusal", "max_tokens"):
            g.errors.append(f"model stopped: {resp.stop_reason}")
            return g
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if getattr(b, "type", "") == "tool_use" and b.name == "submit_op"]
        if not uses:
            messages.append({"role": "user", "content": "Call submit_op with the module source."})
            g.attempts.append({"attempt": attempt, "problems": ["no submission"]})
            continue
        b = uses[0]
        inp = dict(b.input or {})
        src, select, params = inp.get("source") or "", inp.get("select") or {}, inp.get("params") or {}
        problems = check_source(src)
        trials: List[FileTrial] = []
        if not problems:
            for i, f in enumerate(samples):
                t = trial(src, Path(f), select, params, out_dir / "sandbox" / f"attempt{attempt}" / f"{i:02d}")
                trials.append(t)
                problems += [f"{Path(f).name}: {p}" for p in t.problems]
        g.attempts.append({"attempt": attempt, "problems": problems, "trials": [t.to_dict() for t in trials]})
        log(f"codegen attempt {attempt}: {'PASS' if not problems else f'{len(problems)} problem(s)'}")
        if not problems:
            g.source, g.select, g.params, g.passed = src, select, params, True
            g.name = _op_name(src)
            g.path = save_generated(g, out_dir)
            return g
        messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                                                      "content": "FAILED — fix and submit again:\n- "
                                                      + "\n- ".join(problems)[:12000]}]})
    g.errors.append(f"no passing op after {max_iter} attempt(s)")
    return g


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
                                                     "attempts": g.attempts}, indent=2, default=str), encoding="utf-8")
    return p


def run_generated(g: GeneratedOp, inputs: Sequence[str], out_dir: Path, log=None) -> List[FileTrial]:
    """Apply a passing generated op to every input (sandbox per file, verifier per file). Verified outputs are
    copied to ``out_dir/outputs``; records to ``out_dir/records``. Never in place."""
    from .batch import expand_inputs
    log = log or (lambda m: None)
    out_dir = Path(out_dir)
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, f in enumerate(expand_inputs(inputs)):
            t = trial(g.source, f, g.select, g.params, Path(tmp) / f"{i:04d}")
            if t.passed:
                dest = out_dir / "outputs" / f.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(t.output, dest)
                t.output = dest
            else:
                t.output = None
            if t.record is not None:
                rd = out_dir / "records"
                rd.mkdir(parents=True, exist_ok=True)
                (rd / f"{f.stem}.record.md").write_text(t.record.to_markdown(), encoding="utf-8")
            results.append(t)
            log(f"{'PASS' if t.passed else 'FAIL'} {f.name}")
    return results
