"""Pure geometry: SDFs, frames, skeleton landmarks and the base body topology."""

import unittest

import numpy as np

from charlib import body as BD
from charlib import body_mesh as BM
from charlib import head as H
from charlib import sdf as S
from charlib import skeleton as K
from charlib import weights as W
from charlib.frames import Polyline, rmf
from charlib.proportions import BODIES, BUILDS, NEUTRAL

REGIONS = set(BD.REGIONS)


class SdfTests(unittest.TestCase):
    def test_raycast_hits_sphere(self):
        f = lambda p: S.sphere(p, (0.0, 0.0, 0.0), 0.5)  # noqa: E731
        dirs = np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0, -1.0], [0.6, 0.8, 0]])
        t = S.raycast(f, np.zeros((4, 3)), dirs, t_max=2.0)
        np.testing.assert_allclose(t, 0.5, atol=1e-5)

    def test_smooth_union_is_below_min(self):
        a = np.linspace(-1, 1, 50)
        b = a[::-1]
        self.assertTrue(np.all(S.smin(a, b, 0.2) <= np.minimum(a, b) + 1e-12))

    def test_flat_capsule_is_closed(self):
        p = np.array([[0.0, 0.0, 2.0], [0.0, 0.0, -1.0]])
        d = S.flat_capsule(p, (0, 0, 0), (0, 0, 1), (0.2, 0.1), (0.2, 0.1), (0, 1, 0))
        self.assertTrue(np.all(d > 0.5))

    def test_rmf_does_not_flip(self):
        line = Polyline([np.array([0.0, 0, 0]), np.array([0.0, 0, 1]), np.array([0.3, 0, 1.4])])
        ts = [line.tangent(s) for s in np.linspace(0, line.length, 20)]
        ns = rmf(ts, np.array([1.0, 0, 0]))
        for a, b in zip(ns, ns[1:]):
            self.assertGreater(float(a @ b), 0.5)  # turns with the tangent, never flips


class SkeletonTests(unittest.TestCase):
    def test_bones_are_symmetric_and_inside_height(self):
        for body in BODIES.values():
            bones = K.joint_map(body)
            for name, bone in bones.items():
                self.assertGreater(np.linalg.norm(bone.tail - bone.head), 0.005, name)
                self.assertLessEqual(bone.tail[2], body.height + 1e-6, name)
                if name.endswith(".L"):
                    mirror = bones[name[:-2] + ".R"]
                    np.testing.assert_allclose(mirror.head, K.mirror(bone.head), atol=1e-9)

    def test_landmarks_match_spec(self):
        for body in BODIES.values():
            sp = K.spine_points(body)
            self.assertAlmostEqual(sp[-1][2], body.height, places=6)
            self.assertGreater(body.hip_joint[2], body.crotch_z)


class BodyTopologyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layouts = {bid: BD.BodyLayout(b) for bid, b in BODIES.items()}

    def test_closed_manifold_within_budget(self):
        for bid, lay in self.layouts.items():
            edges = {}
            for f, _ in lay.faces:
                for i in range(len(f)):
                    e = tuple(sorted((f[i], f[(i + 1) % len(f)])))
                    edges[e] = edges.get(e, 0) + 1
            bad = [e for e, n in edges.items() if n != 2]
            self.assertEqual(bad, [], f"{bid}: open or non-manifold edges")
            tris = sum(len(f) - 2 for f, _ in lay.faces)
            self.assertLessEqual(tris, 12000, f"{bid}: LOD0 body budget")

    def test_builds_keep_topology_and_joints(self):
        lay = self.layouts["body_a"]
        base = lay.positions(NEUTRAL)
        used = sorted({v for f, _ in lay.faces for v in f})  # not the arm-hole interiors
        for name, build in BUILDS.items():
            p = lay.positions(build)
            self.assertEqual(p.shape, base.shape)
            moved = np.linalg.norm(p - base, axis=1)[used]
            self.assertLess(moved.max(), 0.08, name)
            self.assertGreater(moved.max(), 0.005, name)

    def test_face_keys_only_move_the_head(self):
        lay = self.layouts["body_b"]
        fp = H.FaceParams(**lay.face_defaults())
        base = lay.positions(NEUTRAL, fp)
        p = lay.positions(NEUTRAL, H.apply_key(fp, H.FACE_KEYS["face_jaw_wide"]))
        moved = np.linalg.norm(p - base, axis=1) > 1e-4
        z = base[moved, 2]
        self.assertTrue(len(z) > 0)
        self.assertGreater(z.min(), lay.b.neck_base_z - 0.02)

    def test_meshdata_regions_weights(self):
        lay = self.layouts["body_a"]
        md = BM.body_meshdata(lay, with_keys=False)
        self.assertEqual(set(md.region), REGIONS)
        for w in md.weights:
            self.assertTrue(w, "unweighted vertex")
            self.assertLessEqual(len(w), W.MAX_INFLUENCES)
            self.assertAlmostEqual(sum(w.values()), 1.0, places=5)
            self.assertTrue(all(v >= W.MIN_WEIGHT - 1e-9 for v in w.values()))
        names = {n for w in md.weights for n in w}
        self.assertTrue(all(n.startswith("DEF-") for n in names))
        # No vertex is left unused after the arm holes are compacted.
        used = np.zeros(len(md.verts), dtype=bool)
        for f in md.faces:
            used[list(f)] = True
        self.assertTrue(used.all())


class OpeningTests(unittest.TestCase):
    """The edge of a front opening (V-necks, open shirts) in a 40-column trunk row."""

    def test_open_front_finds_the_first_kept_column_each_side(self):
        from charlib.gear import _opening_col

        kept = [True] * 40
        for c in (0, 1, 2, 38, 39):
            kept[c] = False
        self.assertEqual(_opening_col(kept, 1), 3)
        self.assertEqual(_opening_col(kept, -1), 37)

    def test_arm_holes_are_not_an_opening(self):
        from charlib.gear import _opening_col

        # A closed front with the arm holes beside the shoulders: no opening.
        kept = [True] * 40
        for c in (11, 29):
            kept[c] = False
        self.assertIsNone(_opening_col(kept, 1))
        self.assertIsNone(_opening_col(kept, -1))


if __name__ == "__main__":
    unittest.main()
