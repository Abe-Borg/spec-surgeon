"""The Anthropic API key, remembered in Windows Credential Manager (never in ``settings.json``).

Uses pywin32's ``win32cred`` (bundled with the Windows app and the ``word`` extra). Where it is not available every
function reports that plainly and the window keeps the key for the session only.

Precedence when the window starts: an ``ANTHROPIC_API_KEY`` already in the environment (``setx``) → the saved key →
none (typed in Settings for this session only).
"""
from __future__ import annotations

import os
from typing import Optional

TARGET = "Spec Surgeon/ANTHROPIC_API_KEY"
ENV = "ANTHROPIC_API_KEY"


def _win32cred():
    try:
        import win32cred  # type: ignore[import-not-found]
        return win32cred
    except ImportError:
        return None


def available() -> bool:
    return _win32cred() is not None


def load(target: str = TARGET) -> Optional[str]:
    wc = _win32cred()
    if wc is None:
        return None
    try:
        cred = wc.CredRead(target, wc.CRED_TYPE_GENERIC, 0)
    except Exception:  # pywintypes.error: ERROR_NOT_FOUND
        return None
    blob = cred.get("CredentialBlob")
    if isinstance(blob, bytes):
        blob = blob.decode("utf-16-le")
    return blob or None


def save(key: str, target: str = TARGET) -> None:
    wc = _win32cred()
    if wc is None:
        raise RuntimeError("Windows Credential Manager is not available in this Python")
    wc.CredWrite({"Type": wc.CRED_TYPE_GENERIC, "TargetName": target, "UserName": "Spec Surgeon",
                  "CredentialBlob": key, "Persist": wc.CRED_PERSIST_LOCAL_MACHINE,
                  "Comment": "Anthropic API key for Spec Surgeon typed requests"}, 0)


def delete(target: str = TARGET) -> bool:
    wc = _win32cred()
    if wc is None:
        return False
    try:
        wc.CredDelete(target, wc.CRED_TYPE_GENERIC, 0)
        return True
    except Exception:
        return False


def key_source() -> str:
    """Where the key the next compile will use comes from: "environment" | "saved" | "session" | "none".
    ("session" = typed into the window and not saved; the window tracks that itself.)"""
    if not os.environ.get(ENV):
        return "none"
    saved = load()
    return "saved" if saved and saved == os.environ.get(ENV) else "environment"
