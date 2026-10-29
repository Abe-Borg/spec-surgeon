"""Stand-ins for the renderer seam. The app renders through ``render.render_pdf_groups`` (a run's sources and copies
in one call; ``render.render_pdfs`` is its one-group form), so a test that fakes a renderer per folder of files wraps
its fake with ``per_group`` and patches ``render_pdf_groups``: every caller, grouped or not, then reaches the fake."""
from __future__ import annotations


def per_group(render_pdfs):
    """``render_pdf_groups`` from a per-folder ``render_pdfs(files, out_dir, backend, workers, log=, detail=, cache=)``
    fake: one call of it per group, in order."""
    def render_pdf_groups(groups, backend, workers=4, log=None, detail=None, cache=None):
        return [render_pdfs(files, out_dir, backend, workers, log=log, detail=detail, cache=cache)
                for files, out_dir in groups]
    return render_pdf_groups


def per_group_backend(render_pdfs):
    """``libreoffice.render_groups`` from a per-folder ``libreoffice.render_pdfs(files, out_dir, workers=, log=,
    detail=)`` fake."""
    def render_groups(groups, workers=4, timeout=600, log=None, detail=None):
        return [render_pdfs(files, out_dir, workers=workers, log=log, detail=detail) for files, out_dir in groups]
    return render_groups
