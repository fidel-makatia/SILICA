#!/usr/bin/env python3
"""Tool-agnosticism, checked rather than asserted.

Runs the SAME conformance corpus as the reference backend against KLayout. A
new backend is finished when this passes with its own factory. Skips cleanly
if the klayout python module is absent.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                ".."))

try:
    import klayout.db  # noqa: F401
except ImportError:
    try:
        import pya  # noqa: F401
    except ImportError:
        print("SKIP: klayout python module not installed (pip install klayout)")
        sys.exit(0)

from conformance import run_all  # noqa: E402
from silica.backends.klayout import KLayoutBackend  # noqa: E402

fails = run_all(KLayoutBackend, "klayout")
print("----")
print("ALL PASS" if fails == 0 else "%d FAILURES" % fails)
sys.exit(1 if fails else 0)
