"""The Spec Surgeon window (PySide6 / Qt 6). ``ui.app.main`` is the entry point (``spec-surgeon-gui``).

Nothing outside this package imports PySide6 at module level (``gui.main`` imports it inside the function): the
command line, ``gui.Session`` and the process-pool workers must load without Qt. ``ui/rows.py`` is Qt-free too.
"""
