"""Runs the character pipeline tests and exits non-zero on failure.

    blender -b --factory-startup --python tools/blender/characters/tests/run_tests.py
    blender -b --factory-startup --python tools/blender/characters/tests/run_tests.py -- --assets client/public/assets

The asset tests (test_assets.py) check exported files and skip when the assets
root has no characters/manifest.json yet.
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True  # keep __pycache__ out of the source tree
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if "--assets" in argv:
    os.environ["SHS_CHARACTER_ASSETS"] = os.path.abspath(argv[argv.index("--assets") + 1])

sys.path.append(os.path.dirname(os.path.dirname(HERE)))  # tools/blender, for pins
import pins  # noqa: E402

pins.require_blender()  # tools/blender/BLENDER_VERSION

suite = unittest.defaultTestLoader.discover(HERE, pattern="test_*.py")
result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
