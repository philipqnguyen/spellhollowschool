"""Runs the tools/blender unit tests and exits non-zero on failure.

    blender -b --factory-startup --python tools/blender/tests/run_tests.py
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, HERE)

sys.path.append(os.path.dirname(HERE))  # tools/blender, for pins
import pins  # noqa: E402

pins.require_blender()  # tools/blender/BLENDER_VERSION

suite = unittest.defaultTestLoader.discover(HERE, pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
