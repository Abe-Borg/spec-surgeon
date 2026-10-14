"""Operation library (docs/NL_SURGICAL_EDITOR_PLAN.md §4). Add an op: write a module with an Op subclass,
import it here, add it to OPS. jobs/ keeps thin aliases over these for the v0.1.0 CLI."""
from __future__ import annotations

from typing import Dict, Type

from .base import (BlockDiff, JobException, Op, OpException, Plan, PlanFragment, assign_revision_ids,
                   check_conflicts, merge_fragments)

OPS: Dict[str, Type[Op]] = {}


def register(cls: Type[Op]) -> Type[Op]:
    OPS[cls.name] = cls
    return cls


def get_op(name: str) -> Op:
    if name not in OPS:
        raise KeyError(f"unknown op {name!r}; known: {sorted(OPS)}")
    return OPS[name]()


__all__ = ["Op", "OpException", "JobException", "Plan", "PlanFragment", "BlockDiff", "OPS", "get_op",
           "register", "merge_fragments", "check_conflicts", "assign_revision_ids"]

# op modules register themselves on import (keep after register() is defined)
from . import (apply_header_footer_template, delete_paragraphs, fix_page_numbering,  # noqa: E402,F401
               insert_paragraph, remove_header_footer, replace_text, set_doc_defaults_font,
               set_font, set_page_setup, set_paragraph_props, set_style_font)
