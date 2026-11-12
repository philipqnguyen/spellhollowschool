"""Unit tests for the guardian animals (tools/blender/guardian/
guardian_models.py): the six animals, their part names and joints, the
triangle budget, closed outward-facing parts and the manifest the client
reads. Pure Python (no bpy):

    python3 -I -m unittest discover -s tools/blender/guardian/tests
    blender -b --factory-startup --python tools/blender/guardian/tests/run_tests.py
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(HERE))

import guardian_models as pm  # noqa: E402

QUADRUPED = ["body", "head", "leg_fl", "leg_fr", "leg_bl", "leg_br", "tail"]
BIRD = ["body", "head", "wing_l", "wing_r", "tail"]


def signed_volume(mesh) -> float:
    v = mesh.verts
    total = 0.0
    for a, b, c in mesh.tris:
        pa, pb, pc = v[a], v[b], v[c]
        total += (pa[0] * (pb[1] * pc[2] - pb[2] * pc[1]) - pa[1] * (pb[0] * pc[2] - pb[2] * pc[0])
                  + pa[2] * (pb[0] * pc[1] - pb[1] * pc[0])) / 6.0
    return total


class AnimalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.animals = {a.key: a for a in pm.animals()}

    def test_six_animals_with_their_gaits(self):
        self.assertEqual(list(self.animals), ["hare", "fox", "cat", "owl", "otter", "horse"])
        gaits = {k: a.gait for k, a in self.animals.items()}
        self.assertEqual(gaits, {"hare": "hop", "fox": "trot", "cat": "glide", "owl": "fly", "otter": "swim",
                                 "horse": "gallop"})

    def test_part_names(self):
        for key, a in self.animals.items():
            names = [p.name for p in a.parts]
            self.assertEqual(names, BIRD if key == "owl" else QUADRUPED, key)
            for p in a.parts:
                self.assertEqual(pm.node_name(key, p.name), f"guardian_{key}_{p.name}")

    def test_triangle_budget(self):
        for key, a in self.animals.items():
            self.assertLessEqual(a.triangles(), pm.TRI_BUDGET, key)
            self.assertGreater(a.triangles(), 300, key)

    def test_parts_are_closed_and_face_out(self):
        for key, a in self.animals.items():
            for p in a.parts:
                edges = {}
                for t in p.mesh.tris:
                    for i in range(3):
                        e = (t[i], t[(i + 1) % 3])
                        edges[e] = edges.get(e, 0) + 1
                for (x, y), n in edges.items():
                    self.assertEqual(n, 1, (key, p.name, "edge used twice the same way"))
                    self.assertIn((y, x), edges, (key, p.name, "open edge"))
                self.assertGreater(signed_volume(p.mesh), 0, (key, p.name, "inside out"))

    def test_pivots_are_joints(self):
        for key, a in self.animals.items():
            lo, hi = a.bounds()
            for p in a.parts:
                # Every pivot lies within the animal, and its part's geometry
                # around it (the joint is on the part).
                for k in range(3):
                    self.assertTrue(lo[k] - 1e-6 <= p.pivot[k] <= hi[k] + 1e-6, (key, p.name, k))
                plo, phi = p.mesh.bounds()
                for k in range(3):
                    self.assertTrue(plo[k] - 0.02 <= 0.0 <= phi[k] + 0.02, (key, p.name, "joint off its part", k))
            body = a.part("body")
            head = a.part("head")
            tail = a.part("tail")
            self.assertGreater(head.pivot[2], body.pivot[2], key)
            self.assertLess(tail.pivot[2], body.pivot[2], key)
            if key == "owl":
                self.assertGreater(a.part("wing_l").pivot[0], 0)
                self.assertLess(a.part("wing_r").pivot[0], 0)
                continue
            # Legs: left is +x, front ahead of back.
            fl, fr, bl, br = (a.part(n).pivot for n in pm.LEGS)
            self.assertGreater(fl[0], 0)
            self.assertLess(fr[0], 0)
            self.assertGreater(bl[0], 0)
            self.assertLess(br[0], 0)
            self.assertGreater(fl[2], bl[2])
            self.assertAlmostEqual(fl[0], -fr[0])

    def test_ground_animals_stand_on_the_ground(self):
        for key, a in self.animals.items():
            lo, _ = a.bounds()
            if a.follow_height_m == 0:
                self.assertAlmostEqual(lo[1], 0.0, delta=0.012, msg=key)
                for leg in pm.LEGS:
                    p = a.part(leg)
                    plo, _ = p.mesh.bounds()
                    self.assertAlmostEqual(plo[1] + p.pivot[1], 0.0, delta=0.012, msg=(key, leg))

    def test_sizes(self):
        size = {k: a.size() for k, a in self.animals.items()}
        # Small animals stay under knee-to-waist height; the horse is a pony.
        for k in ("hare", "fox", "cat"):
            self.assertLess(size[k][1], 0.7, k)
        self.assertGreater(size["horse"][1], 1.3)
        self.assertLess(size["horse"][1], 1.7)
        self.assertGreater(size["owl"][0], 0.7)  # wingspan

    def test_manifest(self):
        man = pm.manifest(list(self.animals.values()))
        json.dumps(man)  # serialisable
        self.assertEqual(man["glb"], "guardian.glb")
        self.assertEqual(set(man["animals"]), set(pm.ANIMALS))
        for key, entry in man["animals"].items():
            a = self.animals[key]
            self.assertEqual(entry["parts"], [p.name for p in a.parts])
            self.assertEqual(set(entry["pivots"]), set(entry["parts"]))
            self.assertEqual(entry["nodes"]["body"], f"guardian_{key}_body")
            self.assertIn(entry["gait"], pm.GAITS)
            self.assertEqual(len(entry["size_m"]), 3)
            self.assertEqual(entry["triangles"], a.triangles())
        self.assertEqual(man["animals"]["owl"]["follow_height_m"], 1.45)
        self.assertGreater(man["animals"]["otter"]["follow_height_m"], 0)

    def test_built_manifest_matches_when_present(self):
        path = os.path.join(HERE, "..", "..", "..", "..", "client", "public", "assets", "guardian", "manifest.json")
        if not os.path.exists(path):
            self.skipTest("guardian assets not built")
        with open(path, encoding="utf-8") as f:
            built = json.load(f)
        self.assertEqual(built, json.loads(json.dumps(pm.manifest(list(self.animals.values())))))


if __name__ == "__main__":
    unittest.main()
