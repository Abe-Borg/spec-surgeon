"""Shared, configurable defaults for sustained document investigation.

These are allowances, not targets to consume. Hard verification and input/output
boundaries remain independent of how much reasoning a task is allowed to use.
"""
from __future__ import annotations

import hashlib
import json

DEFAULT_MAX_ATTEMPTS = 50
DEFAULT_MAX_CALLS = 512
DEFAULT_MAX_COST_USD = 250.0
DEFAULT_MAX_OUTPUT_TOKENS = 64000
DEFAULT_MAX_TOTAL_TOKENS = 20000000
DEFAULT_MAX_IMAGE_TOKENS = 1000000
DEFAULT_SAMPLE_FILES = 8
# Working input before history rollover, not the investigation's total-token budget.
# Opus 5.5 has a 1M context window: retain substantial evidence while leaving room
# for the 64k response and estimation error. Smaller-window model overrides should
# also set max_context_tokens explicitly. Prioritize capability; tune costs later.
DEFAULT_CONTEXT_TOKENS = 900000
DEFAULT_ANALYSIS_SECONDS = 120
DEFAULT_ANALYSIS_MEMORY_MB = 1024
DEFAULT_ANALYSIS_OUTPUT_BYTES = 16 * 1024 * 1024
DEFAULT_ANALYSIS_INPUT_BYTES = 128 * 1024 * 1024
DEFAULT_EFFORT = "max"
DEFAULT_STALL_REPETITIONS = 3


def positive_integer(name: str, value: int) -> int:
    """Reject ambiguous settings without imposing an arbitrary small ceiling."""
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer (not a boolean)")
    return value


class InvestigationProgress:
    """Detect equivalent failed candidates without new inspection evidence.

    Different candidates can keep making progress. Re-reading identical evidence,
    changing a tool id, or alternating between already-failed candidates cannot
    indefinitely reset the stall guard. Callers supply executable candidate data,
    omitting descriptive prose that has no effect on the attempted operation.
    """

    def __init__(self, repetitions: int = DEFAULT_STALL_REPETITIONS):
        self.repetitions = positive_integer("stall_repetitions", repetitions)
        self._evidence = set()
        self._failures = {}

    @staticmethod
    def _key(value) -> str:
        data = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def observe(self, evidence) -> bool:
        key = self._key(evidence)
        if key in self._evidence:
            return False
        self._evidence.add(key)
        self._failures.clear()
        return True

    def rejected(self, candidate, errors) -> bool:
        # Diagnostic paths, line numbers and model explanations can vary while the
        # attempted operation is unchanged. Only new evidence or a different
        # executable candidate counts as progress; changing an error string does not.
        key = self._key(candidate)
        self._failures[key] = self._failures.get(key, 0) + 1
        return self._failures[key] >= self.repetitions
