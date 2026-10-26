"""Operation library. Add an op: write a module with an Op subclass
(``plan``, ``apply`` and ``describe`` — the sentence the plan shows for it, over ``prose.py``), decorate it with
``register`` and import it here."""
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
               set_font, set_page_setup, set_paragraph_props, set_properties, set_style_font)
from . import (agree, end_of_section, fill_variables, filter_tagged, naming,  # noqa: E402,F401  — built-in batch 1
               replace_table, resolve_options, swap_image)
from . import (comments, docprops, empty_paragraphs, hidden, highlight, links,  # noqa: E402,F401  — built-in batch 2
               revisions, watermark)
from . import set_tab_stops  # noqa: E402,F401  — built-in batch 3
from . import remove_unused_styles  # noqa: E402,F401  — built-in batch 4
from . import set_borders_shading  # noqa: E402,F401  — built-in batch 4
from . import clear_formatting, set_language  # noqa: E402,F401  — built-in batch 4 (items 28, 29)
from . import map_styles  # noqa: E402,F401  — built-in batch 4 (style mapping over ops/style_import.py)
from . import apply_style  # noqa: E402,F401  — built-in batch 4 (item 30, over clear_formatting and style_import)
from . import apply_table_style, set_table_props  # noqa: E402,F401  — built-in batch 5 (tables, items 37, 38)
from . import audit_numbering, set_list_level  # noqa: E402,F401  — built-in batch 5 (list levels and the audit, items 35, 36)
from . import convert_numbering, convert_typed_numbers  # noqa: E402,F401  — built-in batch 5 (numbering, items 33, 34)
from . import hygiene  # noqa: E402,F401 — character hygiene, items 41–44
from . import add_comments  # noqa: E402,F401 — literal approved review reasons
from . import insert_section_break, remove_section_break  # noqa: E402,F401 — built-in batch 6 (sections)
from . import move_paragraphs  # noqa: E402,F401 — built-in batch 6 (F9: moving)
from . import delete_table_rows, delete_tables, insert_table, insert_table_rows  # noqa: E402,F401 — built-in batch 6 (F5)
from . import delete_table_columns, merge_table_cells, set_table_cells  # noqa: E402,F401 — built-in batch 6 (F6)
from . import insert_image, remove_images, resize_images  # noqa: E402,F401 — built-in batch 6 (F4, pictures)
from . import comment_threads, insert_field  # noqa: E402,F401 — built-in batch 6 (fields and comment threads, §9)
from . import remove_footnotes  # noqa: E402,F401 — built-in batch 6 (F2; delete_paragraphs grew over ops/deletion.py)
