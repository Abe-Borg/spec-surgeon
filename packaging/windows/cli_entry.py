"""Console entry point sharing the standalone app's bundled runtime."""
import sys

from launcher_bootstrap import prepare

if __name__ == "__main__":
    prepare()
    from spec_surgeon.cli import main

    sys.exit(main())
