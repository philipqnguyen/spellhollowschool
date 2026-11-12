"""The broom prop (charlib/broom.py) and the riding clips.

The geometry tests need no assets; the export tests run on the assets root
given to run_tests.py and skip when the broom has not been exported there.
"""

import json
import os
import unittest

import numpy as np

from charlib import anim as AN
from charlib import broom as BR
from charlib import gltf_inspect as G

ASSETS = os.environ.get("SHS_CHARACTER_ASSETS", os.path.join(BR.REPO, "client", "public", "assets"))
GLB = os.path.join(ASSETS, BR.FILE)
MANIFEST = os.path.join(ASSETS, "characters", "manifest.json")


def signed_volume(m, roles) -> float:
    v = np.array(m.verts)
    vol = 0.0
    for f, r in zip(m.faces, m.roles):
        if r not in roles:
            continue
        for k in range(1, len(f) - 1):
            a, b, c = v[f[0]], v[f[k]], v[f[k + 1]]
            vol += float(np.dot(a, np.cross(b, c))) / 6.0
    return vol


class BroomGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = BR.build()
        cls.v = np.array(cls.m.verts)

    def test_under_the_triangle_budget(self):
        self.assertLessEqual(self.m.tris(), BR.MAX_TRIANGLES)
        self.assertGreater(self.m.tris(), 150)  # a handle and a bundle of twigs, not a stick

    def test_colours_come_from_the_palette(self):
        with open(BR.PALETTE_PATH, encoding="utf-8") as f:
            ids = {c["id"] for c in json.load(f)["colours"]}
        for role, cid in BR.PALETTE.items():
            self.assertIn(cid, ids, role)
        self.assertEqual(set(self.m.roles), set(BR.PALETTE))

    def test_handle_under_the_origin_forward_along_minus_y(self):
        # Blender axes: forward is -y (glTF +z); the origin is the rider's hips bone.
        handle = self.v[[i for f, r in zip(self.m.faces, self.m.roles) if r == "handle" for i in f]]
        top = handle[(handle[:, 1] > -0.6) & (handle[:, 1] < BR.HANDLE_BACK)][:, 2].max()  # the straight part
        self.assertAlmostEqual(top, -BR.SEAT_BELOW, delta=0.002)
        self.assertAlmostEqual(handle[:, 1].min(), -BR.HANDLE_FRONT - 0.008, delta=0.01)
        self.assertLess(abs(handle[:, 0]).max(), BR.HANDLE_R + 1e-6)
        twigs = self.v[[i for f, r in zip(self.m.faces, self.m.roles) if r.startswith("twig") for i in f]]
        self.assertGreater(twigs[:, 1].min(), BR.BINDING[0] - 0.03)  # all behind the rider
        self.assertLess(twigs[:, 2].max(), 0.05)  # the bundle stays about the handle, under the hips bone

    def test_closed_parts_face_outward(self):
        self.assertGreater(signed_volume(self.m, {"handle"}), 0.0)

    def test_hands_rest_on_the_handle_ahead_of_the_seat(self):
        self.assertLess(BR.GRIPS[0], BR.GRIPS[1])
        self.assertLess(BR.GRIPS[1], BR.HANDLE_FRONT - 0.2)


class BroomClipTests(unittest.TestCase):
    def test_clips_registered_looping(self):
        clips = {c.name: c for c in AN.CLIPS}
        for name in ("broom_ride", "broom_hover"):
            self.assertIn(name, clips)
            self.assertTrue(clips[name].loop)
            self.assertEqual(clips[name].meta.get("broom"), "DEF-spine")
            self.assertEqual(clips[name].meta.get("hands"), "broom_handle")
        meta = AN.clip_meta()
        self.assertEqual(meta["broom_hover"]["frames"], 90)

    def test_riding_hips_keep_the_toes_over_the_root(self):
        from charlib import proportions as P

        for body_id in ("body_a", "body_b"):
            b = P.get(body_id)
            hip_z = AN._broom_hip_z(b)
            leg = float(np.linalg.norm(np.array(b.hip_joint) - np.array(b.ankle)))
            ankle_z = hip_z - AN.BROOM_RIDE["ankle_drop"] * leg
            self.assertGreater(ankle_z, b.ankle[2], body_id)  # the ankle over its own height off the floor
            self.assertLess(hip_z, b.hip_joint[2], body_id)  # seated lower than standing


@unittest.skipUnless(os.path.exists(GLB), f"no exported broom in {ASSETS}")
class BroomExportTests(unittest.TestCase):
    def test_one_static_mesh_with_vertex_colours(self):
        g = G.read_glb_json(GLB)
        self.assertEqual(len(g["meshes"]), 1)
        prims = g["meshes"][0]["primitives"]
        self.assertEqual(len(prims), 1)  # one draw
        self.assertIn("COLOR_0", prims[0]["attributes"])
        self.assertNotIn("skins", g)
        self.assertNotIn("animations", g)
        self.assertNotIn("images", g)
        tris = g["accessors"][prims[0]["indices"]]["count"] // 3
        self.assertLessEqual(tris, BR.MAX_TRIANGLES)
        node = g["nodes"][0]
        self.assertFalse(any(k in node for k in ("translation", "rotation", "scale")))

    def test_manifest_entry(self):
        with open(MANIFEST, encoding="utf-8") as f:
            entry = json.load(f).get("broom")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["file"], BR.FILE)
        self.assertEqual(entry["attach"], "DEF-spine")
        self.assertEqual(entry["forward"], "+z")
        self.assertLessEqual(entry["triangles"], BR.MAX_TRIANGLES)
        lo, hi = entry["bounds"]["min"], entry["bounds"]["max"]
        self.assertGreater(hi[2], 0.8)  # the handle forward along +z
        self.assertLess(lo[2], -1.0)  # the twigs behind
        self.assertLess(hi[1], 0.0)  # all under the hips bone


if __name__ == "__main__":
    unittest.main()
