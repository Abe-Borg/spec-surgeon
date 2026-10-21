import sys

from .gui import main

if __name__ == "__main__":  # required on Windows: process-pool workers re-import the main module
    sys.exit(main())
