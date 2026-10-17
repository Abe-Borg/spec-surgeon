"""Handle frozen workers and isolated investigation subprocesses before opening any UI."""
import multiprocessing
import os
import sys


def prepare():
    # Windowed executables have no standard streams; libraries may still write progress to them.
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
        elif hasattr(stream, "reconfigure"):
            # Redirected Windows streams default to an ANSI code page that cannot represent
            # the help text, report arrows, or common Unicode document filenames.
            stream.reconfigure(encoding="utf-8")
    multiprocessing.freeze_support()
    if sys.argv[1:4] == ["-I", "-m", "spec_surgeon.sandbox_runner"]:
        from spec_surgeon.sandbox_runner import main

        sys.exit(main(sys.argv[4:]))
    if sys.argv[1:4] == ["-I", "-m", "spec_surgeon.analysis_runner"]:
        from spec_surgeon.analysis_runner import main

        sys.exit(main(sys.argv[4:]))
    if sys.argv[1:2] == ["--installer-smoke-test"]:
        from installed_smoke import main

        sys.exit(main(sys.argv[2]))
    # The window and the command line (never a pool worker or the sandbox, which returned above) hold the app
    # mutex for their whole life: an in-app update waits for it, and uninstall refuses while it exists.
    from spec_surgeon.winapp import hold_app_mutex

    hold_app_mutex()
