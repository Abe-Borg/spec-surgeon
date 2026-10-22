"""Windowed entry point for the standalone Windows app."""
from launcher_bootstrap import prepare

if __name__ == "__main__":
    prepare()
    from spec_surgeon.gui import run

    run()
