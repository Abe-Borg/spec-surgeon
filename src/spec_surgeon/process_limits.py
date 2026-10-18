"""OS resource ceilings for generated transforms; unavailable isolation fails closed."""
from __future__ import annotations

import ctypes
import sys

DEFAULT_MEMORY_MB = 2048
DEFAULT_OUTPUT_BYTES = 256 * 1024 * 1024
DEFAULT_LOG_BYTES = 1024 * 1024
MAX_RESULT_BYTES = 32768
_WINDOWS_JOBS = []


class SandboxLimitError(RuntimeError):
    code = "resource-limit"


def install_limits(*, memory_mb, seconds, output_bytes):
    memory = memory_mb * 1024 * 1024
    if sys.platform.startswith("linux"):
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (seconds + 1, seconds + 2))
        file_bytes = max(output_bytes, 65536)  # leave room to report a small configured document limit
        resource.setrlimit(resource.RLIMIT_FSIZE, (file_bytes, file_bytes))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        return {"platform": "linux", "memory_bytes": memory, "file_bytes": file_bytes,
                "cpu_seconds": seconds + 1, "wall_timeout": "parent"}
    if sys.platform == "win32":
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
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        job = kernel.CreateJobObjectW(None, None)
        settings = Extended()
        settings.basic.flags = 0x00000100 | 0x00000008 | 0x00002000  # memory, one process, kill on close
        settings.basic.active = 1
        settings.process_memory = memory
        if (not job or not kernel.SetInformationJobObject(job, 9, ctypes.byref(settings), ctypes.sizeof(settings))
                or not kernel.AssignProcessToJobObject(job, kernel.GetCurrentProcess())):
            if job:
                kernel.CloseHandle(job)
            raise SandboxLimitError("cannot enforce generated-transform memory limits with a Windows Job Object")
        _WINDOWS_JOBS.append(job)
        return {"platform": "windows", "memory_bytes": memory, "wall_timeout": "parent",
                "file_limit": "checked before writing; no OS file-size ceiling"}
    raise SandboxLimitError("generated-transform memory isolation is unavailable on this platform")


class BoundedText:
    """Bound normal Python diagnostics before writing them to the parent's log files."""

    def __init__(self, stream, limit):
        self.stream, self.limit, self.used = stream, limit, 0

    def write(self, text):
        size = len(text.encode("utf-8", "replace"))
        if self.used + size > self.limit:
            raise SandboxLimitError("generated-transform diagnostics exceeded their configured output allowance")
        self.used += size
        return self.stream.write(text)

    def flush(self):
        return self.stream.flush()

    @property
    def encoding(self):
        return self.stream.encoding
