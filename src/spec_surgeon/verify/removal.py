"""V10 kind(s) removed_hidden_text, removed_notes, removed_empty_paragraphs — built-in batch 2, verifier side (docs/BUILTIN_OPS_BATCH2_DESIGN.md). (Being built.)"""
from typing import Any, Callable, Dict

REMOVAL_KINDS: Dict[str, Callable[..., Any]] = {}
