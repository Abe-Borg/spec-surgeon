"""Isolated read-only analyzer process. No live paths are exposed to generated code.

Linux uses a syscall allowlist in addition to the restricted language, safe XML
parser and audit guard. Windows uses a process/memory Job Object plus the language
and audit guards. Isolation setup fails closed before executing generated code.
"""
from __future__ import annotations

import builtins
import ctypes
import errno
import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


def install_readonly_guard():
    from .sandbox_runner import BLOCKED_EVENTS, BLOCKED_PREFIXES, PATH_EVENTS

    def hook(event, args):
        if (event == "open" or event in PATH_EVENTS or event in BLOCKED_EVENTS
                or event.startswith(BLOCKED_PREFIXES + ("socket.",))
                or event in ("os.listdir", "os.scandir", "os.chdir", "os.fchdir")):
            raise PermissionError(f"analysis cannot access the filesystem, network or processes ({event})")

    sys.dont_write_bytecode = True
    sys.addaudithook(hook)


def _linux_limits(limits):
    import resource
    memory = limits["memory_mb"] * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (limits["seconds"], limits["seconds"] + 1))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits["output_bytes"] + 4096,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Every syscall not explicitly needed for in-memory Python/XML computation is
    # denied, including open/openat, sockets, fork/exec, ptrace and io_uring.
    library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    context = library.seccomp_init(0x00050000 | errno.EPERM)
    if not context:
        raise RuntimeError("cannot initialize the analysis syscall guard")
    try:
        names = ("read", "write", "readv", "writev", "close", "fstat", "fstat64", "lseek", "_llseek",
                 "mmap", "mmap2", "mprotect", "munmap", "mremap", "madvise", "brk", "futex", "futex_time64",
                 "sched_yield", "getpid", "gettid", "getuid", "geteuid", "getgid", "getegid", "getrandom",
                 "clock_gettime", "clock_gettime64", "gettimeofday", "time", "getrusage", "times",
                 "rt_sigaction", "rt_sigprocmask", "rt_sigreturn", "sigaltstack", "restart_syscall",
                 "exit", "exit_group")
        for name in names:
            number = library.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and library.seccomp_rule_add(context, 0x7FFF0000, number, 0):
                raise RuntimeError(f"cannot restrict analysis syscall {name}")
        if library.seccomp_load(context):
            raise RuntimeError("cannot install the analysis syscall guard")
    finally:
        library.seccomp_release(context)
    return {"platform": "linux", "syscalls": "allowlist", "memory_bytes": memory}


def _windows_limits(limits):
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD), ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t),
                    ("active", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6),
                    ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]

    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    job = kernel.CreateJobObjectW(None, None)
    settings = Extended()
    settings.basic.flags = 0x00000100 | 0x00000008 | 0x00002000  # process memory, active process, kill on close
    settings.basic.active = 1
    settings.process_memory = limits["memory_mb"] * 1024 * 1024
    if (not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(settings), ctypes.sizeof(settings))
            or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess())):
        raise RuntimeError("cannot install the analysis Windows Job Object")
    # Keep the job handle alive until process exit; generated code never receives it.
    return {"platform": "windows", "job_object": True, "memory_bytes": settings.process_memory}


def prepare_runtime():
    from .investigation import ANALYSIS_FROM, ANALYSIS_IMPORTS
    from .sandbox_runner import SAFE_BUILTINS
    from lxml import etree

    def fromstring(text, *args, **kwargs):
        if args or set(kwargs) - {"base_url"}:
            raise ValueError("analysis XML parsing accepts only XML text")
        if isinstance(text, str):
            text = text.encode("utf-8")
        return etree.fromstring(text, parser=etree.XMLParser(resolve_entities=False, no_network=True,
                                                            load_dtd=False, huge_tree=False))

    safe_etree = SimpleNamespace(fromstring=fromstring, tostring=etree.tostring, QName=etree.QName,
                                iselement=etree.iselement, Element=etree.Element, SubElement=etree.SubElement)
    modules = {name: importlib.import_module(name) for name in ANALYSIS_IMPORTS | set(ANALYSIS_FROM)}
    modules["lxml"] = SimpleNamespace(etree=safe_etree)

    def importer(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name not in modules:
            raise ImportError(f"analysis import {name!r} is not available")
        if fromlist and (name not in ANALYSIS_FROM or any(x not in ANALYSIS_FROM[name] for x in fromlist)):
            raise ImportError(f"analysis import from {name!r} is not available")
        return modules[name]

    excluded = {"hasattr", "object", "print", "super", "classmethod", "staticmethod", "property", "format", "callable"}
    safe = {name: getattr(builtins, name) for name in SAFE_BUILTINS if name not in excluded and hasattr(builtins, name)}
    safe["__import__"] = importer
    return {"__builtins__": safe, "__name__": "generated_analysis"}


def main(argv=None):
    request_path = Path((argv or sys.argv[1:])[0])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    from .investigation import AnalysisLimits, check_analysis_source
    limits = AnalysisLimits(**request["limits"])
    output = (request_path.parent / "result.json").open("wb")  # opened before the irrevocable read-only guard
    result = {"ok": False}
    try:
        problems = check_analysis_source(request["source"])
        if problems:
            raise ValueError("analysis source rejected: " + "; ".join(problems))
        namespace = prepare_runtime()
        compiled = compile(request["source"], "generated_analysis.py", "exec")
        if sys.platform.startswith("linux"):
            isolation = _linux_limits(request["limits"])
        elif sys.platform == "win32":
            isolation = _windows_limits(request["limits"])
        else:
            raise RuntimeError("read-only analysis isolation is unavailable on this platform")
        install_readonly_guard()
        exec(compiled, namespace)
        value = namespace["analyze"](request["documents"], request["inputs"])
        result = {"ok": True, "result": value, "isolation": isolation}
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
        if len(encoded) > limits.output_bytes:
            raise ValueError("analysis output exceeded its configured allowance")
    except BaseException as error:
        result = {"ok": False, "error": f"{type(error).__name__}: {str(error)[:2000]}"}
        encoded = json.dumps(result).encode()
    output.write(encoded)
    output.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
