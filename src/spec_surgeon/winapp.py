"""Small Windows integration helpers (ctypes only; every function is a harmless no-op on other platforms).

- **App mutex.** Every Spec Surgeon process that can hold files of the installed app open (the window and the
  ``spec-surgeon.exe`` command line) holds the named mutex ``APP_MUTEX`` until the process ends (never released
  early: Windows drops it when the process is really gone). The installer waits for it to disappear before
  replacing files during an in-app update, refuses to uninstall while it exists, and the window refuses to
  start an update while another instance holds it. Pool workers and the Tier B sandbox never take it.
- **AppUserModelID**, so the taskbar groups the window under the Start-menu shortcut (which carries the same id).
- **message_box**, for errors before Qt exists (a windowed launcher has no console to print to).

``packaging/windows/installer.iss`` repeats ``APP_MUTEX`` and ``AUMID``; ``tests/test_packaging.py`` pins them.
"""
from __future__ import annotations

import sys
from typing import Optional

APP_MUTEX = "SpecSurgeon-97A6079B-7F70-4F32-A691-12624B86E88C"
AUMID = "AbrahamBorg.SpecSurgeon"

_SYNCHRONIZE = 0x00100000
_handle: Optional[int] = None


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _kernel32():
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    k.CreateMutexW.restype = wintypes.HANDLE
    k.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    k.OpenMutexW.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    return k


def hold_app_mutex() -> bool:
    """Create (or open) the app mutex and keep the handle for the life of the process. Idempotent."""
    global _handle
    if not _is_windows():
        return False
    if _handle:
        return True
    try:
        h = _kernel32().CreateMutexW(None, False, APP_MUTEX)
    except (OSError, AttributeError):
        return False
    _handle = h or None
    return bool(_handle)


def release_app_mutex() -> None:
    global _handle
    if _is_windows() and _handle:
        try:
            _kernel32().CloseHandle(_handle)
        except (OSError, AttributeError):
            pass
    _handle = None


def mutex_exists() -> bool:
    if not _is_windows():
        return False
    try:
        k = _kernel32()
        h = k.OpenMutexW(_SYNCHRONIZE, False, APP_MUTEX)
    except (OSError, AttributeError):
        return False
    if h:
        k.CloseHandle(h)
        return True
    return False


def other_instances_running() -> bool:
    """True when another Spec Surgeon window or command holds the app mutex (ours is released for the probe and
    taken again)."""
    if not _is_windows():
        return False
    held = _handle is not None
    release_app_mutex()
    try:
        return mutex_exists()
    finally:
        if held:
            hold_app_mutex()


def set_app_user_model_id(aumid: str = AUMID) -> bool:
    if not _is_windows():
        return False
    try:
        import ctypes
        return ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(aumid) == 0
    except (OSError, AttributeError):
        return False


def get_app_user_model_id() -> Optional[str]:
    if not _is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes
        out = wintypes.LPWSTR()
        if ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(out)) != 0:
            return None
        value = out.value
        ctypes.windll.ole32.CoTaskMemFree(out)
        return value
    except (OSError, AttributeError):
        return None


def message_box(title: str, text: str) -> None:
    """A native message box on Windows (MB_ICONERROR); elsewhere nothing (the caller also prints to stderr)."""
    if not _is_windows():
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, title, 0x10)
    except (OSError, AttributeError):
        pass
