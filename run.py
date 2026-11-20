"""Entry point for PyInstaller (it can't run `python -m qeplus`)."""

import sys

from qeplus.app import main

sys.exit(main())
