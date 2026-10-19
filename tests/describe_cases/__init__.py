"""Describe cases, one module per operation family; tests/test_describe.py collects every module's CASES."""
from __future__ import annotations

import importlib
import pkgutil


def all_cases() -> dict:
    cases: dict = {}
    for module in pkgutil.iter_modules(__path__):
        found = importlib.import_module(f"{__name__}.{module.name}").CASES
        for name, items in found.items():
            cases.setdefault(name, []).extend(items)
    return cases
