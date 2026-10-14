"""The remembered API key (Windows Credential Manager). Runs only where ``win32cred`` exists; uses a throw-away
target so a real saved key is never touched."""
from __future__ import annotations

import uuid

import pytest

from spec_surgeon import credentials

pytestmark = pytest.mark.skipif(not credentials.available(), reason="Windows Credential Manager not available")


def test_round_trip_on_a_throwaway_target():
    target = f"Spec Surgeon/test-{uuid.uuid4().hex}"
    try:
        assert credentials.load(target) is None
        credentials.save("sk-ant-not-a-real-key-ü", target)
        assert credentials.load(target) == "sk-ant-not-a-real-key-ü"
    finally:
        credentials.delete(target)
    assert credentials.load(target) is None and credentials.delete(target) is False
