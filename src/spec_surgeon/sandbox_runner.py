"""Tier B sandbox runner — executed as ``python -I -m spec_surgeon.sandbox_runner REQUEST.json`` in a separate
process (codegen.run_in_sandbox), in a temporary working directory, with a timeout and a stripped environment.

It re-checks the generated source (codegen.check_source), executes it with a reduced set of builtins and an import
hook that only admits the whitelisted modules, finds the one ``Op`` subclass, and on the copy of the source file
it was given runs ``plan`` (predict first) and ``apply``; it writes ``out.docx`` and ``plan.json`` next to the
request. The parent process verifies the result with the independent verifier — the generated code never sees it.

This is a prototype sandbox: it keeps an honest model inside the engine's API; it is not a security boundary
against hostile code (the process boundary, the copy-only inputs and the timeout are the real limits).
"""
from __future__ import annotations

import builtins
import json
import sys
import traceback
from pathlib import Path

SAFE_BUILTINS = (
    "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "frozenset", "hasattr", "int",
    "isinstance", "issubclass", "iter", "len", "list", "map", "max", "min", "next", "object", "print", "range",
    "repr", "reversed", "round", "set", "slice", "sorted", "str", "sum", "super", "tuple", "zip", "classmethod",
    "staticmethod", "property", "Exception", "ValueError", "KeyError", "IndexError", "TypeError", "StopIteration",
    "AttributeError", "RuntimeError", "NotImplementedError", "True", "False", "None", "divmod", "chr", "ord",
    "format", "hash", "callable",
)


def _guarded_import(allowed):
    def imp(name, globals=None, locals=None, fromlist=(), level=0):
        if level != 0 or not any(name == a or name.startswith(a + ".") for a in allowed):
            raise ImportError(f"import of {name!r} is not allowed in a generated op")
        return builtins.__import__(name, globals, locals, fromlist, level)
    return imp


def load_op_class(source: str):
    from .codegen import ALLOWED_MODULES, check_source
    from .ops.base import Op
    problems = check_source(source)
    if problems:
        raise ValueError("source rejected: " + "; ".join(problems))
    safe = {k: getattr(builtins, k) for k in SAFE_BUILTINS if hasattr(builtins, k)}
    safe["__build_class__"] = builtins.__build_class__
    safe["__import__"] = _guarded_import(ALLOWED_MODULES)
    ns = {"__builtins__": safe, "__name__": "generated_op"}
    exec(compile(source, "generated_op.py", "exec"), ns)
    classes = [v for v in ns.values() if isinstance(v, type) and issubclass(v, Op) and v is not Op]
    if len(classes) != 1:
        raise ValueError(f"the source must define exactly one Op subclass, found {len(classes)}")
    return classes[0]


def main(argv=None) -> int:
    req_path = Path((argv or sys.argv[1:])[0])
    req = json.loads(req_path.read_text(encoding="utf-8"))
    out_dir = req_path.parent
    result = {"ok": False}
    try:
        from .ops.base import merge_fragments
        from .package import DocxPackage
        cls = load_op_class(req["source"])
        op = cls()
        pkg = DocxPackage(Path(req["docx"]).read_bytes(), name=Path(req["docx"]).name)
        frag = op.plan(pkg, req.get("select") or {}, req.get("params") or {})
        frag.op = frag.op or getattr(cls, "name", "generated")
        plan = merge_fragments([frag], job=f"generated:{getattr(cls, 'name', 'op')}", params=req.get("params") or {})
        work = pkg.copy()
        op.apply(work, frag)
        (out_dir / "out.docx").write_bytes(work.output_bytes())
        d = plan.to_dict()
        d.pop("transform_data", None)
        (out_dir / "plan.json").write_text(json.dumps(d, default=str), encoding="utf-8")
        result = {"ok": True, "op_name": getattr(cls, "name", None), "mode": plan.mode}
    except Exception as e:  # everything is reported, nothing is raised past the process boundary
        code = getattr(e, "code", None)
        result = {"ok": False, "error": f"{type(e).__name__}: {e}", "code": code,
                  "traceback": traceback.format_exc()[-4000:]}
    (out_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
