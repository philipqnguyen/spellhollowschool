"""The hairstyles' lock data (charlib/hair.py, hair_styles.py;
character-adoption.md section 4): the first six (ids 14-19) have locks, the
second set (ids 20-24) are Meshy only (hair_meshy.MESHY_ONLY).

Data tests always run. The geometry tests build every style on the shipped
body it belongs to and need that body's saved build
(`art/characters/<body>.blend`, or `SHS_CHARACTER_BLENDS=<dir>`); they skip
without it.
"""

import os
import unittest

import numpy as np

from charlib import content
from charlib import hair as HR
from charlib import hair_styles as HS

BLENDS = os.environ.get("SHS_CHARACTER_BLENDS", os.path.join(content.REPO, "art", "characters"))
SHIPPED = [s for s in content.creator()["hair_styles"] if not s.get("retired")]
OLD_ASSETS = {"short_crop", "tousled", "side_part", "waves_shoulder", "long_straight", "plait_single", "ponytail_high",
              "bun_low", "curly_crop", "box_braids", "afro", "pixie", "close_crop", "curls_long"}
BONES = {f"hair.back.{i:02d}" for i in range(1, 5)} | {f"hair.side.{s}.{i:02d}" for s in "LR" for i in range(1, 4)} | {
    f"hair.front.{i:02d}" for i in range(1, 3)}


class HairDataTests(unittest.TestCase):
    def test_shipped_styles_one_body_each(self):
        from charlib import hair_meshy as HM

        # (Id 25 was removed on 2026-11-07; ids are never reused.)
        self.assertEqual(sorted(s["id"] for s in SHIPPED), list(range(14, 25)))
        for s in SHIPPED:
            name = s["asset"].split("/")[-1]
            # Locks for the first six; the Meshy-only ones have none.
            if name in HM.MESHY_ONLY:
                self.assertNotIn(name, HS.STYLES, name)
            else:
                self.assertIn(name, HS.STYLES, name)
            self.assertEqual(HS.BODY[name], s["body"], name)
            self.assertIn(HS.LENGTH[name], HR.BUDGETS, name)
            self.assertNotIn(name, OLD_ASSETS)
        by_body = {}
        for s in SHIPPED:
            by_body.setdefault(s["body"], []).append(s["id"])
        self.assertEqual({k: len(v) for k, v in by_body.items()}, {"body_a": 6, "body_b": 5})

    def test_retired_styles_migrate_to_each_body(self):
        shipped = {s["id"]: s["body"] for s in SHIPPED}
        retired = [s for s in content.creator()["hair_styles"] if s.get("retired")]
        self.assertEqual(sorted(s["id"] for s in retired), list(range(14)))
        for s in retired:
            self.assertNotIn("asset", s)
            for body, target in s["migrate_to"].items():
                self.assertEqual(shipped[target], body, s["id"])

    def test_content_lists_only_a_bodys_own_styles(self):
        for body in ("body_a", "body_b"):
            self.assertEqual({HS.BODY[s["asset"].split("/")[-1]] for s in content.hair_styles(body)}, {body})

    def test_budgets_shrink_per_lod(self):
        for length, b in HR.BUDGETS.items():
            self.assertEqual(len(b), 4)
            self.assertTrue(all(x > y for x, y in zip(b, b[1:])), length)

    def test_periodic_curve_is_periodic_and_mirrored(self):
        f = HR.periodic([(0, 10.0), (90, 20.0), (180, 30.0)])
        self.assertAlmostEqual(float(f(45.0)), float(f(315.0)), places=6)
        self.assertAlmostEqual(float(f(0.0)), float(f(360.0)), places=6)


def _blend(body):
    p = os.path.join(BLENDS, f"{body}.blend")
    return p if os.path.exists(p) else None


class _StyleBuild:
    """Every style of one body built once (both variants, four LODs)."""

    cache = {}

    @classmethod
    def get(cls, body):
        if body not in cls.cache:
            from charlib import hair_frame as HF

            bb = HF.load_body(_blend(body))
            fr = HF.HeadFrame(bb)
            occ = [(fr.parts[r][0], fr.parts[r][2]) for r in ("head", "scalp", "neck", "torso") if r in fr.parts]
            out = {}
            for s in content.hair_styles(body):
                name = s["asset"].split("/")[-1]
                if name not in HS.STYLES:
                    continue  # Meshy only: no locks to build
                for cap in (False, True):
                    st = HS.style(name, fr, cap)
                    lods = []
                    for lod in range(4):
                        g, paths = HR.build_lod(fr, st, lod, cap)
                        lods.append(HR.finish_lod(fr, st, g, paths, cap, occ))
                    out[(name, cap)] = (st, lods)
            cls.cache[body] = (bb, fr, out)
        return cls.cache[body]


class _GeometryMixin:
    body = ""

    @classmethod
    def setUpClass(cls):
        cls.bb, cls.fr, cls.built = _StyleBuild.get(cls.body)

    def test_budgets_and_lods(self):
        for (name, cap), (st, lods) in self.built.items():
            budget = HR.BUDGETS[st.length]
            tris = [sum(len(f) - 2 for f in hl.faces) for hl in lods]
            for lod, (t, b) in enumerate(zip(tris, budget)):
                self.assertLessEqual(t, b, f"{name} cap={cap} lod{lod}")
            self.assertTrue(all(x > y for x, y in zip(tris, tris[1:])), f"{name} {tris}")

    def test_shade_layer(self):
        for (name, cap), (st, lods) in self.built.items():
            for lod, hl in enumerate(lods):
                sh = hl.shade
                self.assertEqual(sh.shape, (len(hl.V), 4))
                self.assertTrue((sh[:, 0] >= HR.SHADE_FLOOR - 1e-6).all() and (sh[:, 0] <= 1.0 + 1e-6).all(), name)
                self.assertTrue(np.allclose(sh[:, 3], HR.OUTLINE_W), name)
                self.assertTrue(((sh[:, 1] >= 0) & (sh[:, 1] <= 1)).all(), name)
                # y never decreases from root to tip along a lock.
                lock = np.array(hl.geo.lock)
                s = np.array(hl.geo.s)
                for li in np.unique(lock[lock >= 0]):
                    m = np.nonzero(lock == li)[0]
                    order = m[np.argsort(s[m], kind="stable")]
                    y = sh[order, 1]
                    ss = s[order]
                    # Section points of one ring share s: compare ring means.
                    _, idx = np.unique(np.round(ss, 7), return_inverse=True)
                    means = np.array([y[idx == k].mean() for k in range(idx.max() + 1)])
                    self.assertTrue((np.diff(means) >= -1e-6).all(), f"{name} lock {li}")

    def test_angel_ring_on_most_outer_locks(self):
        """The band crosses most top locks at one latitude (a ring, not dashes)."""
        for (name, cap), (st, lods) in self.built.items():
            if cap:
                continue
            hl = lods[0]
            lock = np.array(hl.geo.lock)
            ring = [li for li, lk in enumerate(st.locks) if lk.layer >= 1 and lk.group in ("fringe", "crown", "back", "side")]
            crossing = [li for li in ring if not np.isnan(np.array(hl.geo.s_ring)[lock == li]).all()]
            self.assertGreaterEqual(len(crossing), 0.5 * len(ring), name)

    def test_weights_and_sway_chains(self):
        for (name, cap), (st, lods) in self.built.items():
            bones = {b for c in st.chains for b in HR.chain_bone_names(c)}
            self.assertLessEqual(len(bones), 12, name)
            self.assertTrue(bones <= BONES, name)
            for hl in lods:
                for w in hl.weights:
                    self.assertLessEqual(len(w), 4)
                    self.assertAlmostEqual(sum(w.values()), 1.0, places=5)
                    self.assertTrue(set(w) <= bones | {HR.HEAD_BONE})
            if HS.LENGTH[name] == "long":
                self.assertIn("back", st.chains, name)
            joints = HR.chain_joints(st, lods[0].paths)
            self.assertEqual(sorted(joints), sorted(st.chains), name)

    def test_cap_variant_stays_under_hats(self):
        from charlib.hair_frame import band_phi

        for (name, cap), (st, lods) in self.built.items():
            if not cap:
                continue
            for hl in lods:
                phi, theta, r = self.fr.polar(hl.V)
                above = phi < np.array([band_phi(t) for t in theta])
                # Beside the ears (whose top reaches the band line) hair is
                # not pressed into the ear; no hat covers it there.
                if self.fr.ears is not None:
                    from mathutils import Vector

                    near = np.array([self.fr.ears.find_nearest(Vector(tuple(p)), 0.025)[0] is not None for p in hl.V])
                    above &= ~near
                over = r[above] - self.fr.skull_r(phi[above], theta[above])
                self.assertLessEqual(float(over.max()), HR.CAP_CLEARANCE + 1e-4, name)

    def test_scalp_covered(self):
        """Every hidden scalp vertex is under hair (rays from the head centre)."""
        from mathutils import Vector
        from mathutils.bvhtree import BVHTree

        scalp = self.fr.parts["scalp"][0]
        c = Vector(tuple(self.fr.centre))
        for (name, cap), (st, lods) in self.built.items():
            hl = lods[0]
            tris = [(f[0], f[i], f[i + 1]) for f in hl.faces for i in range(1, len(f) - 1)]
            tree = BVHTree.FromPolygons(hl.V.tolist(), tris, all_triangles=True)
            bare = 0
            for p in scalp:
                d = Vector(tuple(p)) - c
                hit = tree.ray_cast(c + d * 0.98, d.normalized(), 0.3)
                bare += hit[0] is None
            self.assertEqual(bare, 0, f"{name} cap={cap}: {bare} scalp vertices uncovered")

    def test_fringe_hem(self):
        for (name, cap), (st, lods) in self.built.items():
            hem = HR.fringe_hem(self.fr, st, lods[0])
            self.assertTrue(8 <= len(hem) <= 24, f"{name}: {len(hem)}")
            xs = [p[0] for p in hem]
            self.assertEqual(xs, sorted(xs))

    def test_deterministic(self):
        name, cap = next(iter(self.built))
        st = HS.style(name, self.fr, cap)
        a, _ = HR.build_lod(self.fr, st, 1, cap)
        b, _ = HR.build_lod(self.fr, HS.style(name, self.fr, cap), 1, cap)
        self.assertTrue(np.array_equal(np.array(a.verts), np.array(b.verts)))
        self.assertEqual(a.faces, b.faces)

    def test_styles_differ(self):
        """Every style its own silhouette (bounds differ by more than 2 cm somewhere)."""
        boxes = {name: (hl[0].V.min(axis=0), hl[0].V.max(axis=0)) for (name, cap), (st, hl) in self.built.items() if not cap}
        names = sorted(boxes)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                d = max(np.abs(boxes[a][0] - boxes[b][0]).max(), np.abs(boxes[a][1] - boxes[b][1]).max())
                self.assertGreater(d, 0.02, (a, b))


@unittest.skipUnless(_blend("body_a"), f"no body_a.blend in {BLENDS}")
class MaleHairTests(_GeometryMixin, unittest.TestCase):
    body = "body_a"


@unittest.skipUnless(_blend("body_b"), f"no body_b.blend in {BLENDS}")
class FemaleHairTests(_GeometryMixin, unittest.TestCase):
    body = "body_b"


if __name__ == "__main__":
    unittest.main()
