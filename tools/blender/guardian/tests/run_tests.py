"""Runs the guardian unit tests and exits non-zero on failure.

    blender -b --factory-startup --python tools/blender/guardian/tests/run_tests.py
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, HERE)

suite = unittest.defaultTestLoader.discover(HERE, pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
