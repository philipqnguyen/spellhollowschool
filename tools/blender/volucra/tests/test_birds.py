"""Unit tests for Bird Conjuring's birds (tools/blender/volucra/birds_model.py): the
three parts the client looks up by name, a small triangle budget, wings
at their shoulders reaching out to their own sides, a bird about 0.16 m
long facing +z, palette names only, and the conjured feather. Pure Python:

    python3 tools/blender/volucra/tests/test_birds.py
    (or Blender's: blender -b --factory-startup --python <this file>)
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
VOLUCRA = os.path.dirname(HERE)
REPO = os.path.abspath(os.path.join(VOLUCRA, "..", "..", ".."))
sys.path[:0] = [VOLUCRA, os.path.join(os.path.dirname(VOLUCRA), "world")]

import birds_model as bm  # noqa: E402


class BirdTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parts = bm.build()

    def test_part_names_the_client_reads(self):
        self.assertEqual([p.name for p in self.parts], ["bird_body", "bird_wing_l", "bird_wing_r"])
        self.assertEqual(tuple(p.name for p in self.parts), bm.PARTS)

    def test_triangle_budget(self):
        n = bm.triangles(self.parts)
        self.assertLessEqual(n, bm.MAX_TRIS)
        self.assertGreater(n, 60, "a bird, not a token")

    def test_size_and_facing(self):
        lo, hi = self.parts[0].mesh.bounds()
        length = hi[2] - lo[2]
        self.assertTrue(0.12 <= length <= 0.22, length)
        # The beak (forward, +z) is further from the middle than the tail is.
        self.assertGreater(hi[2], 0.08)
        self.assertGreater(-lo[2], 0.08)
        self.assertLess(hi[0] - lo[0], 0.1, "slim body; the wings are their own parts")

    def test_wings_hinge_at_their_shoulders(self):
        _, left, right = self.parts
        self.assertGreater(left.origin[0], 0)
        self.assertLess(right.origin[0], 0)
        self.assertAlmostEqual(left.origin[0], -right.origin[0])
        for part, side in ((left, 1), (right, -1)):
            lo, hi = part.mesh.bounds()
            reach = hi[0] if side > 0 else -lo[0]
            self.assertAlmostEqual(reach, bm.WING_SPAN, places=5)
            # The wing starts at its shoulder (its origin) and never reaches across the body.
            self.assertGreaterEqual(min(v[0] * side for v in part.mesh.verts), -1e-6)

    def test_colours_are_palette_names(self):
        with open(os.path.join(REPO, "data", "art", "castle_textures.json"), encoding="utf-8") as f:
            palette = json.load(f)["palette"]
        for name in bm.ZONES.values():
            self.assertIn(name, palette)
        used = set().union(*(p.mesh.zones() for p in self.parts))
        self.assertEqual(used, set(bm.ZONES))

    def test_manifest(self):
        m = bm.manifest(self.parts, bm.feather())
        self.assertEqual(m["parts"], list(bm.PARTS))
        self.assertEqual(m["triangles"], bm.triangles(self.parts))
        self.assertEqual(set(m["shoulders"]), {"bird_wing_l", "bird_wing_r"})
        self.assertEqual(m["feather"]["part"], "bird_feather")
        self.assertNotIn("feather", bm.manifest(self.parts))

    def test_the_conjured_feather(self):
        """Bird Conjuring's Transfiguration: a small pale feather the client turns into the birds."""
        f = bm.feather()
        self.assertEqual(f.name, bm.FEATHER)
        self.assertNotIn(f.name, bm.PARTS)
        n = f.mesh.triangle_count()
        self.assertLessEqual(n, bm.FEATHER_TRIS)
        self.assertGreaterEqual(n, 16, "a feather, not a card")
        lo, hi = f.mesh.bounds()
        self.assertAlmostEqual(hi[2] - lo[2], bm.FEATHER_LENGTH + 0.012, places=5)
        # Origin about its middle; flat and slim; wider to one side of the quill.
        self.assertLess(abs((hi[2] + lo[2]) / 2), 0.01)
        self.assertLess(hi[1] - lo[1], 0.03)
        self.assertLess(hi[0] - lo[0], 0.05)
        self.assertGreater(hi[0], -lo[0])
        # Pale vane, tan quill: palette zones only.
        self.assertEqual(f.mesh.zones(), {bm.BACK, bm.BREAST, bm.BEAK})
        # With the bird, still inside the whole budget.
        self.assertLessEqual(bm.triangles(self.parts) + n, bm.MAX_TRIS)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]])
