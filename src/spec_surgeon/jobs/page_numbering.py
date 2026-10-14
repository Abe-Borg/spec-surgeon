"""Job ``page-numbering`` — v0.1.0 alias of the op ``fix_page_numbering`` (src/spec_surgeon/ops/)."""
from __future__ import annotations

from .base import OpJob


class PageNumberingJob(OpJob):
    name = "page-numbering"
    op_name = "fix_page_numbering"
