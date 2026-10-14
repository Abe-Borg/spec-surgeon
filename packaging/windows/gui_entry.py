"""Windowed entry point for the standalone Windows app."""
import sys

from launcher_bootstrap import prepare

if __name__ == "__main__":
    prepare()
    from spec_surgeon.gui import main

    sys.exit(main())
