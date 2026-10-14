"""Qt for the window tests: offscreen (no display needed), skipped when PySide6 or a system library it needs is
missing — unless ``SPEC_SURGEON_REQUIRE_QT`` is set (CI sets it, so a broken Qt install fails instead of skipping)."""
from __future__ import annotations

import os

import pytest


def qt_app_or_skip():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError as e:      # PySide6 missing, or e.g. libEGL missing (an ImportError, not ModuleNotFoundError)
        if os.environ.get("SPEC_SURGEON_REQUIRE_QT"):
            raise
        pytest.skip(f"Qt not available: {e}")
    return QApplication.instance() or QApplication([])
