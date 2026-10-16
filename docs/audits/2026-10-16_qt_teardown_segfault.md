# 2026-10-16 — the test suite's segmentation fault: windows left to the garbage collector

Date: 2026-10-16. Branch: `claude/festive-hamilton-5o579l` from `master` at `3a4d661`. Report: `python3 -m pytest -q
tests/test_diagnostics.py tests/test_editspec.py` dies with SIGSEGV (exit 139, sometimes SIGABRT) in
`tests/test_editspec.py::test_run_spec_batch_24_files[remove-all-headers-footers]`, while the main process waits in
`concurrent.futures.as_completed` for its four pool workers; the full suite the same, but only sometimes; four pool
workers left orphaned (PPID 1), holding the output pipe open. Deliverables: this note, `tests/qt_support.destroy_windows`
and the fixtures that use it, the table models parented to their page (`ui/pages.py`), `tests/test_qt_lifetime.py`.

## 1. Reproduction

The pair, three times in the container (PySide6 6.12.0, Python 3.13.16, Qt offscreen): one pass (69 tests, 22 s), two
crashes, each leaving four orphaned workers. Then a probe outside pytest, deterministic — three crashes in three:

```python
w = build_window(Session(out_root=...), prompts=RecordingPrompts())   # as tests/test_diagnostics.py's qt fixture
w._spec_surgeon["wait"]()
w.force_close = True; w.close()                                       # the fixture's whole teardown
del w
threading.Thread(target=gc.collect).start().join()                    # a collection on another thread …
for i in range(200000): x = [i] * 3                                   # … then Python on the main thread
```

```
Thread 1 "python3" received signal SIGSEGV, Segmentation fault.
#0  0x0000000000000002 in ?? ()
#1  Shiboken::BindingManager::runDeletionInMainThread() () from libshiboken6.abi3.so.6.12
#2  ?? () from libshiboken6.abi3.so.6.12          (mainThreadDeletionHandler, the pending call)
#3… CPython
```

The window's Python object is freed on the worker thread (a `weakref.finalize` says so); the main thread dies the next
time it runs Python. Neither a preview nor a run is needed: a window built and closed is enough.

## 2. Mechanism

Shiboken 6.12 (`basewrapper.cpp`, `SbkDeallocWrapperCommon`): when a wrapper that owns a valid C++ object dies on a
thread other than the main one and its type is marked `delete_in_main_thread`, the destructor is not run; a
`DestructorEntry{cpp_dtor, cptr}` is queued and `Py_AddPendingCall(mainThreadDeletionHandler)` makes the main thread
run the queue (`runDeletionInMainThread`: a plain loop over the entries) the next time it executes Python. Probing
PySide6 6.12.0 with a child `QObject` whose `destroyed` signal records the deleting thread:

| dropped on a worker thread | C++ object deleted |
|---|---|
| `QWidget`, `QMainWindow`, `QDialog` | later, on the main thread (deferred) |
| `QObject`, `QTimer` | at once, on the worker |
| a Python subclass of any of them (`class Win(QMainWindow)`) | **at once, on the worker** — the flag is not inherited by heap types |
| a child with a Qt parent (any type) | not at all (C++ owns it) |

So when the cyclic garbage collector frees a test's `MainWindow` on another thread — which it does whenever a
collection happens to run there; in `test_editspec.py` the pool's manager thread allocates while the main thread
waits — Shiboken deletes the whole Qt window tree right there (`delete MainWindowWrapper`, a thread Qt never allowed to
delete widgets from), and in the same collection the plain Qt widgets of that tree whose wrappers Shiboken still books
as Python-owned are deferred. 44 of them in a window (gdb on `addToDeletionInMainThread`, joined with the wrappers'
`getCppPointer`): the `QPushButton`s, `QLabel`s and `QCheckBox`es built without a parent and placed through layouts
(under `Card`, `UpdateBanner`, `ChatView`, the status bar) — `ownedByPython()` True, a Qt parent, and not invalidated
when the window went, because the window's deletion never ran Shiboken's child invalidation for them. Qt had already
deleted them with their parent; the deferred `delete` then runs on freed memory, the virtual destructor is read from
garbage and the call lands at address 2.

Why it looked random: whether the collection that frees the windows runs on the main thread (every deletion immediate,
in a safe order — no crash) or on another thread depends on which thread's allocations cross the collector's threshold.
Why `tests/test_gui.py`'s windows never crashed anything: its fixture called `deleteLater()` and `processEvents()`, which
deletes nothing outside a running event loop (Qt holds a DeferredDelete back until control returns to the loop that
posted it); it merely moved ownership to C++, so the collector found nothing of Qt's to delete — the windows leaked.

## 3. Fix

- **`tests/qt_support.destroy_windows(app, windows)`**: for each window, wait while a job, update check or download
  thread is still running (bounded; at the deadline the window is kept alive in `KEPT_ALIVE`, never deleted — its
  thread still emits through the window's `Bridge`/`UpdateController` — and a `TimeoutError` names it; Codex's finding
  on the PR), `force_close`, `close()`, `deleteLater()`; then `QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)` and
  `processEvents()` — the Qt objects are deleted now, on the main thread, in Qt's order, while the test still holds
  every wrapper, so every wrapper is invalid before any collector sees it; then `gc.collect()` on the main thread, so
  any other Qt object the test dropped is freed here and not on a job or pool thread later. Every `qt` fixture
  (`test_diagnostics.py`, `test_gui.py`, `test_phase8_fixes.py`, `test_phase8_fixes_window.py`, `test_library.py`) and
  the one try/finally window test tear down through it.
- **`ui/pages.py`**: `RowsModel` and `ProblemsFilter` get their page as parent — the only QObjects the window made
  without one (Python subclasses, so the collector would have deleted them on whatever thread it ran).
- **`tests/test_qt_lifetime.py`** (6 tests): after the teardown no wrapper of the window or of anything it owns is valid;
  the models belong to their page; a window whose job still runs at the deadline is kept alive and named, never deleted
  under its thread; the crash's scenario (teardown, then a collection on a worker thread, then Python on
  the main thread) survives, in a subprocess; every test module that builds a window imports `destroy_windows`; the
  window's garbage after the teardown holds only invalid wrappers.

## 4. Proof

Recorded in the README's Tests section and the PR: the two-file pair and the full suite, three times each, no orphaned
pytest processes left behind.

## 5. Left as is

The real app is not affected: its window lives until the process ends and every QObject it makes has a parent in it
(or goes to Qt by `deleteLater()`), so nothing of Qt's is left to the collector while a job thread runs. PySide's side
of this (a Python subclass not inheriting `delete_in_main_thread`; a deferred deletion of an object its parent already
deleted) is PySide's to fix; the rule here does not depend on it.
