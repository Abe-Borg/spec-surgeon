"""Tier B sandbox runner — executed as ``python -I -m spec_surgeon.sandbox_runner REQUEST.json`` in a separate
process (codegen.run_in_sandbox), in a temporary working directory, with a timeout and a stripped environment.

It re-checks the generated source (codegen.check_source), executes it with a reduced set of builtins and an import
hook that only admits the whitelisted modules, finds the one ``Op`` subclass, and on the copy of the source file
it was given runs ``plan`` (predict first) and ``apply``; it writes ``out.docx`` and ``plan.json`` next to the
request. The parent process verifies the result with the independent verifier — the generated code never sees it.

Before the generated source is executed, ``install_guard`` adds a Python audit hook (defence in depth under the
whitelist): from then on this process may write, remove or rename only inside its own working directory, and may not
start a process, open a socket or load native code. lxml's C-level file access is not audited — the whitelist
(``codegen.FORBIDDEN_ATTRS``, ``MODULE_ATTRS``) is what keeps generated code away from those writers.

This is a prototype sandbox: it keeps an honest model inside the engine's API; it is not a security boundary
against hostile code (the process boundary, the copy-only inputs, the timeout, the whitelist and the audit hook are
the limits — none of them is an OS-level jail).
"""
from __future__ import annotations

import builtins
import json
import os
import sys
import traceback
from pathlib import Path

# Audit events that take paths and change the file system: allowed only inside the working directory.
PATH_EVENTS = {"os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.truncate", "os.symlink",
               "os.link", "os.utime", "os.chflags", "os.lchflags", "os.setxattr", "os.removexattr", "shutil.rmtree",
               "shutil.copyfile", "shutil.copytree", "shutil.move", "shutil.chown", "shutil.copymode",
               "shutil.copystat", "shutil.make_archive", "shutil.unpack_archive"}
# Audit events that are never allowed once the generated code may run.
BLOCKED_EVENTS = {"subprocess.Popen", "os.system", "os.fork", "os.forkpty", "os.kill", "os.killpg", "os.startfile",
                  "os.posix_spawn", "socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg",
                  "socket.getaddrinfo", "ctypes.dlopen", "ctypes.dlsym", "ctypes.call_function", "ctypes.cdata",
                  "webbrowser.open", "urllib.Request", "ftplib.connect", "http.client.connect", "smtplib.connect"}
BLOCKED_PREFIXES = ("os.exec", "os.spawn", "winreg.", "msvcrt.")


def install_guard(workdir) -> None:
    """From here on this process may change the file system only inside ``workdir`` (and may not start processes,
    open sockets or load native code). Audit hooks cannot be removed again."""
    root = os.path.normcase(os.path.realpath(workdir))
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

    def inside(p) -> bool:
        try:
            p = os.fspath(p)
        except TypeError:
            return False                      # a file descriptor or anything else that is not a path
        if isinstance(p, bytes):
            p = os.fsdecode(p)
        full = os.path.normcase(os.path.realpath(p))
        return full == root or full.startswith(root + os.sep)

    def hook(event, args):
        if event == "open":
            path, mode, flags = (list(args) + [None, None, None])[:3]
            writing = ((isinstance(mode, str) and any(c in mode for c in "wax+"))
                       or (isinstance(flags, int) and flags & write_flags))
            if writing and path is not None and not inside(path):
                raise PermissionError(f"the sandbox may not write outside its folder: {path}")
        elif event in PATH_EVENTS:
            for p in args:
                if isinstance(p, (str, bytes, os.PathLike)) and not inside(p):
                    raise PermissionError(f"the sandbox may not change {p} ({event})")
        elif event in BLOCKED_EVENTS or event.startswith(BLOCKED_PREFIXES):
            raise PermissionError(f"the sandbox may not do this: {event}")

    sys.dont_write_bytecode = True              # imports from here on never write .pyc files outside the folder
    sys.addaudithook(hook)

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


def load_op_class(source: str, goal_contract=None):
    from .codegen import ALLOWED_IMPORT, allowed_imports, check_source
    from .ops.base import Op
    problems = check_source(source, goal_contract=goal_contract)
    if problems:
        raise ValueError("source rejected: " + "; ".join(problems))
    safe = {k: getattr(builtins, k) for k in SAFE_BUILTINS if hasattr(builtins, k)}
    safe["__build_class__"] = builtins.__build_class__
    safe["__import__"] = _guarded_import(tuple(set(allowed_imports(goal_contract)) | ALLOWED_IMPORT))
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
        install_guard(out_dir)                  # before a single line of the generated code runs
        cls = load_op_class(req["source"], req.get("goal_contract"))
        op = cls()
        pkg = DocxPackage(Path(req["docx"]).read_bytes(), name=Path(req["docx"]).name)
        from types import MappingProxyType
        import hashlib
        inputs = {}
        for item in req.get("inputs", []):
            content = Path(item["snapshot"]).read_bytes()
            if hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError("sandbox input snapshot changed: " + item["path"])
            for name in item.get("aliases", [item["path"]]):
                inputs[name] = content
        pkg.inputs = MappingProxyType(inputs)
        # These are fresh job data, never a remembered method's authority. Keep
        # separate copies for each phase; the parent verifies against its originals.
        import copy
        pkg.goal = copy.deepcopy(req.get("goal_contract"))
        pkg.binding = copy.deepcopy(req.get("goal_binding"))
        frag = op.plan(pkg, req.get("select") or {}, req.get("params") or {})
        frag.op = frag.op or getattr(cls, "name", "generated")
        plan = merge_fragments([frag], job=f"generated:{getattr(cls, 'name', 'op')}", params=req.get("params") or {})
        work = pkg.copy()
        work.inputs = pkg.inputs
        work.goal = copy.deepcopy(req.get("goal_contract"))
        work.binding = copy.deepcopy(req.get("goal_binding"))
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
