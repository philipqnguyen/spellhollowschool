"""Vegetation look A (world/foliage.py; docs/architecture.md "Rendering"):
the foliage kit's budgets and LOD chain, the crowns' clearance, hedge runs,
plantings and their manifest rows, the colour rule. Pure Python; run through
tests/run_tests.py."""

import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.dirname(HERE), os.path.join(os.path.dirname(HERE), "world")]

import foliage as FO  # noqa: E402
from castle_kit import grid as G  # noqa: E402


class KitTests(unittest.TestCase):
    def test_budgets_and_lod_chain(self):
        for name, sp in FO.SPECIES.items():
            tris = [FO.species_geo(name, lod).tris for lod in FO.LODS]
            self.assertTrue(all(a > b for a, b in zip(tris, tris[1:])), (name, tris))
            if sp.kind in ("broadleaf", "pine"):
                self.assertLessEqual(tris[0], 6000, name)  # style guide section 6
                self.assertLessEqual(tris[3], 0.1 * tris[0], name)  # LOD3 about 10 %: the impostor
                self.assertLessEqual(tris[1], 0.75 * tris[0], name)

    def test_no_leaf_under_the_crown(self):
        for name, sp in FO.SPECIES.items():
            if sp.kind not in ("broadleaf", "pine"):
                continue
            g = FO.species_geo(name, 0)
            leaves = [p[1] for p, uv in zip(g.pos, g.uv) if not FO.in_tile(uv, "bark")]
            self.assertGreaterEqual(min(leaves), sp.crown_low - 0.05, name)

    def test_normals_and_shade(self):
        for name in FO.SPECIES:
            g = FO.species_geo(name, 0)
            self.assertTrue(all(abs(math.hypot(*n) - 1.0) < 1e-6 for n in g.nrm), name)
            self.assertTrue(all(0.3 <= s <= 1.0 for s in g.shade), name)
            self.assertTrue(all(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0 for u, v in g.uv), name)

    def test_deterministic(self):
        a = FO.build_kit()
        FO.species_geo.cache_clear()
        b = FO.build_kit()
        self.assertEqual({k: (g.pos, g.faces) for k, g in a.items()}, {k: (g.pos, g.faces) for k, g in b.items()})

    def test_colours_are_palette_names(self):
        with open(os.path.join(os.path.dirname(HERE), "world", "foliage.py"), encoding="utf-8") as f:
            self.assertEqual(G.colour_literals(f.read()), [])
        pal = FO.palette()
        self.assertEqual(set(pal), set(FO.PALETTE_NAMES))


class PlantingTests(unittest.TestCase):
    def test_tree_tops_where_the_old_piece_was(self):
        for kind, h in (("round", 6.5), ("broad", 7.5), ("pine", 9.0), ("planter", 4.5)):
            p = FO.tree(kind, h, 3, pos=(1.0, 2.0, 3.0))
            self.assertAlmostEqual(p.scale() * FO.species_top(p.species), h, places=6)
            self.assertEqual(p.t, (1.0, 2.0, 3.0))

    def test_hedge_runs_join_without_gaps(self):
        for length, height, depth in ((2.6, 1.25, 0.7), (6.0, 1.3, 0.75), (12.0, 2.6, 1.1)):
            plants = FO.hedge_run(length, height, depth, 1)
            spans = []
            for p in plants:
                lo, hi = (-FO.hedge_cap(p.species), 0.0) if FO.SPECIES[p.species].kind == "hedge_end" else (0.0, FO.HEDGE_SEG)
                xs = sorted(p.point((x, 0.0, 0.0))[0] for x in (lo, hi))
                spans.append(xs)
            spans.sort()
            self.assertAlmostEqual(spans[0][0], 0.0, places=6)
            self.assertAlmostEqual(spans[-1][1], length, places=6)
            for a, b in zip(spans, spans[1:]):
                self.assertAlmostEqual(a[1], b[0], places=6)
            self.assertAlmostEqual(max(p.point((0.0, FO.SPECIES[p.species].height, 0.0))[1] for p in plants), height, places=6)
            self.assertLessEqual(FO.lod0_tris(plants), FO.HEDGE_TRIS_PER_M * max(length, 2.0))

    def test_place_and_rows(self):
        p = FO.planting("pine_a", (0.0, 0.0, 0.0), math.pi / 2, 2.0)
        q = FO.place(p, lambda v: (v[0] + 10.0, v[1], v[2] - 5.0))
        self.assertEqual(q.t, (10.0, 0.0, -5.0))
        self.assertAlmostEqual(q.ex[2], -2.0)  # glTF Ry: +x turns toward -z
        rows = FO.manifest_rows([q, FO.planting("hedge_a")], min_lod=3)
        self.assertEqual(rows["species"], ["hedge_a", "pine_a"])
        self.assertEqual(len(rows["plants"][0]), 13)
        self.assertEqual(rows["min_lod"], 3)

    def test_solid_faces_are_the_trunk_and_the_hedge_mass(self):
        # world/solids.py cuts these to the walking band (0.35-1.9 m): a trunk, never the crown.
        for kind, h in (("round", 6.5), ("broad", 7.5), ("pine", 9.0)):
            p = FO.tree(kind, h, 1, pos=(10.0, 0.0, 20.0))
            pts = [q for f in FO.solid_faces(p) for q in f]
            self.assertLessEqual(min(q[1] for q in pts), 0.35, kind)
            self.assertGreaterEqual(max(q[1] for q in pts), 1.9, kind)
            cx = sum(q[0] for q in pts) / len(pts)
            cz = sum(q[2] for q in pts) / len(pts)
            r = max(math.hypot(q[0] - cx, q[2] - cz) for q in pts)
            self.assertTrue(0.15 <= r <= 0.35, (kind, r))  # the trunk, never the crown
            self.assertLess(math.hypot(cx - 10.0, cz - 20.0), 0.3, kind)
        plants = FO.hedge_run(6.0, 1.3, 0.75, 1)
        pts = [q for p in plants for f in FO.solid_faces(p) for q in f if 0.35 <= q[1] <= 1.9]
        self.assertLessEqual(max(abs(q[2]) for q in pts), 0.75 / 2 + 0.05)
        self.assertAlmostEqual(min(q[0] for q in pts), 0.0, delta=0.15)
        self.assertAlmostEqual(max(q[0] for q in pts), 6.0, delta=0.15)

    def test_kit_manifest(self):
        m = FO.kit_manifest()
        self.assertEqual(m["file"], FO.KIT_FILE)
        self.assertEqual(set(m["species"]), set(FO.SPECIES))
        for s in m["species"].values():
            self.assertEqual(len(s["tris"]), len(FO.LODS))
            self.assertGreater(s["size"], 0.0)


if __name__ == "__main__":
    unittest.main()
