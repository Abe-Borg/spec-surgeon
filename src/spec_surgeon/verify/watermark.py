"""V10 kind(s) added_watermark, removed_watermark — built-in batch 2, verifier side (docs/BUILTIN_OPS_BATCH2_DESIGN.md). (Being built.)"""
from typing import Any, Callable, Dict

WATERMARK_KINDS: Dict[str, Callable[..., Any]] = {}
