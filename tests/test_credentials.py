"""The remembered API key (Windows Credential Manager). The round trip runs only where ``win32cred`` exists and uses a
throw-away target so a real saved key is never touched. This is the only test file conftest leaves the real Credential
Manager to: every other test sees none (a test run must never read or overwrite the key saved on the machine)."""
from __future__ import annotations

import uuid

import pytest

from spec_surgeon import credentials

# Decided when this file is collected, before any fixture runs: the real answer for this machine.
REAL_CREDENTIAL_MANAGER = credentials.available()


def test_this_file_alone_gets_the_real_credential_manager():
    """conftest's cut (``credentials._win32cred`` replaced) is not applied here, so the round trip below tests the real
    functions; tests/test_phase8_fixes.py proves the cut everywhere else."""
    assert credentials._win32cred.__module__ == "spec_surgeon.credentials"
    assert credentials.available() is REAL_CREDENTIAL_MANAGER


@pytest.mark.skipif(not REAL_CREDENTIAL_MANAGER, reason="Windows Credential Manager not available")
def test_round_trip_on_a_throwaway_target():
    target = f"Spec Surgeon/test-{uuid.uuid4().hex}"
    assert target != credentials.TARGET
    try:
        assert credentials.load(target) is None
        credentials.save("sk-ant-not-a-real-key-ü", target)
        assert credentials.load(target) == "sk-ant-not-a-real-key-ü"
    finally:
        credentials.delete(target)
    assert credentials.load(target) is None and credentials.delete(target) is False
