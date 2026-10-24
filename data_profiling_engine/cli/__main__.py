"""Module entry point so ``python -m data_profiling_engine.cli`` runs the
``profiler-cli`` (M2-T11). ``main()`` lives in :mod:`cli.__init__`; this shim
only forwards ``sys.argv`` and the process exit code."""
from __future__ import annotations

import sys

from data_profiling_engine.cli import main

if __name__ == "__main__":
    sys.exit(main())
