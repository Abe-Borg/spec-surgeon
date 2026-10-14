"""Handle frozen process-pool workers and isolated generated-op subprocesses before opening any UI."""
import multiprocessing
import os
import sys


def prepare():
    # Windowed executables have no standard streams; libraries may still write progress to them.
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    multiprocessing.freeze_support()
    if sys.argv[1:4] == ["-I", "-m", "spec_surgeon.sandbox_runner"]:
        from spec_surgeon.sandbox_runner import main

        sys.exit(main(sys.argv[4:]))
    if sys.argv[1:2] == ["--installer-smoke-test"]:
        from installed_smoke import main

        sys.exit(main(sys.argv[2]))
