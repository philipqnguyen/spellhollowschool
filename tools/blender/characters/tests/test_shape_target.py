"""Baked body shapes (charlib/shape_target.py, charlib/sculpt_wrap.py).

The pure tests need nothing. The target tests read the committed shape
targets (art/characters/shape_targets/*.npz) and skip when the art
repository is not checked out; the sculpts themselves are never needed.
The asset tests check an export built with `--body-shape sculpt` (skip
otherwise).
"""

import json
import math
import os
import unittest

import numpy as np

from charlib import content
from charlib import shape_target as ST
from charlib import skeleton as K
from charlib.proportions import BODIES

TARGET_DIR = os.path.join(content.REPO, ST.DEFAULT_DIR)
ASSETS = os.environ.get("SHS_CHARACTER_ASSETS", os.path.join(content.REPO, "client", "public", "assets"))
MANIFEST = os.path.join(ASSETS, "characters", "manifest.json")
LOD0_BUDGET = 15500  # mh_body.LOD0_BUDGET: the LOD0 tier (character adoption, <= 16,000)


def grid(n: int = 7):
    """A bumpy closed-ish test surface: a grid lifted into a dome."""
    xs = np.linspace(-1.0, 1.0, n)
    V = np.array([[x, y, 0.3 * math.cos(x) * math.cos(y)] for y in xs for x in xs])
    faces = []
    for j in range(n - 1):
        for i in range(n - 1):
            a, b, c, d = j * n + i, j * n + i + 1, (j + 1) * n + i + 1, (j + 1) * n + i
            faces += [(a, b, c), (a, c, d)]
    return V, faces


class PureTests(unittest.TestCase):
    def test_key_transfer_identity(self):
        V, faces = grid()
        key = V + np.array([0.0, 0.0, 0.05]) * (V[:, :1] > 0)
        out = ST.transfer_keys(V, V.copy(), faces, {"k": key})
        np.testing.assert_allclose(out["k"], key, atol=1e-9)

    def test_key_transfer_follows_rotation_and_scale(self):
        V, faces = grid()
        key = V + 0.02 * ST.vertex_normals(V, ST.triangles(faces))
        a = math.radians(30)
        R = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
        V1 = 2.0 * V @ R.T + np.array([0.3, 0.0, 1.0])
        out = ST.transfer_keys(V, V1, faces, {"k": key})
        np.testing.assert_allclose(out["k"] - V1, 2.0 * (key - V) @ R.T, atol=1e-6)

    def test_untouched_vertices_stay_put(self):
        V, faces = grid()
        key = V.copy()
        key[0] += [0.0, 0.0, 0.01]
        V1 = V * 1.1
        out = ST.transfer_keys(V, V1, faces, {"k": key})
        np.testing.assert_allclose(out["k"][1:], V1[1:], atol=1e-12)

    def test_thin_plate_spline_hits_its_landmarks(self):
        from charlib import sculpt_wrap as SW

        rng = np.random.default_rng(3)
        src = rng.uniform(-1, 1, (30, 3))
        dst = src * 1.3 + 0.05 * np.sin(src * 4)
        tps = SW.TPS(src, dst, reg=0.0)
        np.testing.assert_allclose(tps(src), dst, atol=1e-6)

    def test_slice_of_a_cylinder(self):
        from charlib import sculpt_wrap as SW

        n, r = 64, 0.1
        ang = np.linspace(0, 2 * math.pi, n, endpoint=False)
        V = np.array([[r * math.cos(t), r * math.sin(t), z] for z in (0.0, 1.0) for t in ang])
        T = [(i, (i + 1) % n, n + i) for i in range(n)] + [((i + 1) % n, n + (i + 1) % n, n + i) for i in range(n)]
        loops = SW.Mesh(V, np.array(T)).slice(np.array([0, 0, 0.5]), np.array([0, 0, 1.0]))
        self.assertEqual(len(loops), 1)
        self.assertAlmostEqual(loops[0].perimeter, 2 * n * r * math.sin(math.pi / n), places=6)
        np.testing.assert_allclose(loops[0].centroid, [0, 0, 0.5], atol=1e-9)

    def test_body_json_round_trip(self):
        from charlib import proportions as P

        for b in BODIES.values():
            self.assertEqual(P.from_json(P.to_json(b)), b)


def targets():
    if not os.path.isdir(TARGET_DIR):
        return []
    return sorted(f for f in os.listdir(TARGET_DIR) if f.endswith(".npz"))


@unittest.skipUnless(targets(), f"no baked shape targets in {TARGET_DIR}")
class TargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.t = {f[:-4]: ST.load_target(os.path.join(TARGET_DIR, f)) for f in targets()}

    def test_both_bodies_baked(self):
        self.assertTrue({"body_a", "body_b"} <= set(self.t))

    def test_same_topology_as_the_prewrap_mesh(self):
        """The target holds the pre-cut visible mesh (before the face paint
        and the LOD0 decimation): its polygons hash to the stored topology."""
        for name, t in self.t.items():
            sizes, flat = t["poly_sizes"], t["poly_verts"]
            starts = np.concatenate([[0], np.cumsum(sizes)[:-1]])
            polys = [tuple(int(v) for v in flat[a:a + n]) for a, n in zip(starts, sizes)]
            self.assertEqual(ST.topology_hash(polys), t["topology"], name)
            self.assertEqual(len(polys), int(t["n_faces"]), name)
            self.assertEqual(len(t["faces"]), int((sizes - 2).sum()), name)
            self.assertEqual(len(t["regions"]), len(t["faces"]), name)
            self.assertEqual(t["verts"].shape, t["verts_prewrap"].shape, name)
            self.assertTrue(np.isfinite(t["verts"]).all(), name)

    def test_face_anchors(self):
        """The sculpt's eye socket, nose, mouth and chin, in order down the
        face; the socket box round its centre."""
        for name, t in self.t.items():
            f = ST.face_layout(t)
            eye, box = f["eye"], f["box"]
            self.assertGreater(eye[0], 0.015, name)
            self.assertTrue(f["crown"][2] > eye[2] > f["nose"][2] > f["mouth"][2] > f["chin"][2], name)
            self.assertLess(box[0][0], eye[0], name)
            self.assertGreater(box[1][0], eye[0], name)
            self.assertLess(box[2][2], eye[2], name)
            self.assertGreater(box[3][2], eye[2], name)
            self.assertTrue(0.025 < box[1][0] - box[0][0] < 0.07, name)

    def test_spec_height_on_the_floor(self):
        for name, t in self.t.items():
            b0 = BODIES[t["body"].id] if t["body"].id in BODIES else None
            V = t["verts"]
            self.assertAlmostEqual(float(V[:, 2].min()), 0.0, delta=0.002, msg=name)
            self.assertAlmostEqual(float(V[:, 2].max()), t["body"].height, delta=0.002, msg=name)
            if b0 is not None:
                self.assertEqual(t["body"].height, b0.height, name)

    def test_no_folds_or_self_intersections(self):
        for name, t in self.t.items():
            faces = [tuple(f) for f in t["faces"]]
            q = ST.wrap_quality(t["verts_prewrap"].astype(float), t["verts"].astype(float), faces, list(t["regions"]))
            self.assertEqual(q["flipped"], 0, f"{name}: {q}")
            self.assertEqual(q["self_intersections"], 0, f"{name}: {q}")

    def test_left_right_symmetry(self):
        from mathutils import Vector
        from mathutils.bvhtree import BVHTree

        for name, t in self.t.items():
            V = t["verts"].astype(float)
            bvh = BVHTree.FromPolygons(V.tolist(), t["faces"].tolist(), all_triangles=True)
            M = V * np.array([-1.0, 1.0, 1.0])
            d = np.array([bvh.find_nearest(Vector(p))[3] for p in M])
            self.assertLess(float(np.percentile(d, 99)), 0.004, name)

    def test_skeleton_from_the_new_measurements(self):
        for name, t in self.t.items():
            b1 = t["body"]
            b0 = BODIES[b1.id]
            j0, j1 = K.joints(b0), K.joints(b1)
            self.assertEqual([(j.name, j.parent) for j in j0], [(j.name, j.parent) for j in j1], name)
            V = t["verts"]
            # Joints sit inside the flesh: knee, ankle, elbow and wrist within the limb's extent.
            for jn in ("shin.L", "foot.L", "forearm.L", "hand.L"):
                p = next(j.head for j in j1 if j.name == jn)
                near = V[np.linalg.norm(V - p, axis=1) < 0.08]
                self.assertGreater(len(near), 8, f"{name}: no flesh around {jn}")
                lo, hi = near.min(axis=0), near.max(axis=0)
                self.assertTrue(((p > lo) & (p < hi)).all(), f"{name}: {jn} outside its limb")

    def test_credits_and_proportions(self):
        for name, t in self.t.items():
            m = t["meta"]
            for k in ("source", "author", "url", "licence", "adapted"):
                self.assertTrue(m.get(k), f"{name}: meta {k}")
            self.assertEqual(m["licence"], "CC BY 4.0")
            self.assertTrue(5.5 < m["heads_tall"] < 9.0, name)
            # The sculpt's own proportions: within a few percent of the sculpt's head count.
            self.assertAlmostEqual(m["heads_tall"], m["heads_tall_sculpt"], delta=0.25, msg=name)
            credits = open(os.path.join(content.REPO, "art", "CREDITS.md"), encoding="utf-8").read()
            self.assertIn(m["author"], credits, name)
            self.assertIn(m["url"], credits, name)


def shaped_bodies():
    if not os.path.exists(MANIFEST):
        return {}
    with open(MANIFEST, encoding="utf-8") as f:
        m = json.load(f)
    return {k: v for k, v in m["assets"].items() if v.get("kind") == "body" and v.get("shape")}


@unittest.skipUnless(shaped_bodies(), f"no --body-shape sculpt export in {ASSETS}")
class ShapedAssetTests(unittest.TestCase):
    def test_lod0_within_budget(self):
        for key, entry in shaped_bodies().items():
            self.assertLessEqual(entry["triangles"]["lod0"], LOD0_BUDGET, key)
            self.assertLess(entry["shape"]["prewrap_max_dev_mm"], 4.0, key)
            self.assertGreater(entry["triangles"]["lod0"], 0.95 * LOD0_BUDGET * 0.97, key)

    def test_skin_keys_and_joints(self):
        from charlib import gltf_inspect as G

        with open(MANIFEST, encoding="utf-8") as f:
            bones = json.load(f)["skeleton"]["bones"]
        for key, entry in shaped_bodies().items():
            glb = os.path.join(ASSETS, entry["file"])
            self.assertEqual(G.joint_names(glb), bones, key)
            names = set()
            for mesh in G.summary(glb)["meshes"]:
                self.assertIn("JOINTS_0", mesh["attributes"], key)
                self.assertIn("COLOR_0", mesh["attributes"], key)
                names |= set(mesh["target_names"])
            self.assertFalse({n for n in names if n.startswith("build_")}, f"{key}: build keys (every body is the neutral build)")
            for k in ("blink_l", "blink_r", "smile", "frown",
                      "mouth_open", "face_jaw_wide", "face_eyes_round"):
                self.assertIn(k, names, f"{key}: shape key {k}")
