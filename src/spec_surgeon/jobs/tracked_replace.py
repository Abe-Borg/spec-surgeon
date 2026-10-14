"""Job ``tracked-replace`` — v0.1.0 alias of the op ``replace_text`` (src/spec_surgeon/ops/).

``full_text_map``/``live_segments`` (the transform-side text maps CLAUDE.md names) live in ops/replace_text.py
and are re-exported here."""
from __future__ import annotations

from ..ops.replace_text import Segment, default_author, full_text_map, live_segments  # noqa: F401
from .base import OpJob


class TrackedReplaceJob(OpJob):
    name = "tracked-replace"
    op_name = "replace_text"
