"""Solid furniture and props (world/solids.py; docs/architecture.md "Layouts"):
the blockers measured off the pieces, the layouts'
`solids` lists kept current, and what is and is never solid."""

import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.path.dirname(HERE)
for _p in (BLENDER, os.path.join(BLENDER, "world")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import layout_lib as L  # noqa: E402
import solids as S  # noqa: E402

_GEN = None


def generated():
    """Every layout's (blockers, report), built once for the module."""
    global _GEN
    if _GEN is None:
        _GEN = S.generate()
    return _GEN


def by_id(lid: str) -> dict:
    return {b.id: b for b in generated()[lid][0]}


def box_faces(x0, y0, z0, x1, y1, z1):
    """The six faces of an axis-aligned box (world points)."""
    c = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    idx = ((0, 1, 3, 2), (4, 5, 7, 6), (0, 1, 5, 4), (2, 3, 7, 6), (0, 2, 6, 4), (1, 3, 7, 5))
    return [[c[i] for i in f] for f in idx]


def cylinder_faces(cx, cz, r, y0, y1, n=16):
    out = []
    for k in range(n):
        a, b = 2 * math.pi * k / n, 2 * math.pi * (k + 1) / n
        p, q = (cx + r * math.cos(a), cz + r * math.sin(a)), (cx + r * math.cos(b), cz + r * math.sin(b))
        out.append([(p[0], y0, p[1]), (q[0], y0, q[1]), (q[0], y1, q[1]), (p[0], y1, p[1])])
    return out


def floors(size=20.0):
    layout = L.parse_layout({"version": 1, "surfaces": [
        {"id": "floor", "kind": "room", "name": "Room", "level": 0, "cell": "room", "min": [-size, -size],
         "max": [size, size], "y": 0}]})
    return S.Floors(layout)


class CutTests(unittest.TestCase):
    """The measuring rule on made-up pieces."""

    def test_a_table_is_its_footprint_and_a_rug_or_a_banner_nothing(self):
        f = floors()
        table = S.blockers_of("t", "table", box_faces(-1.0, 0.0, -0.5, 1.0, 0.76, 0.5), (0, 0, 0), 0.0, f)
        self.assertEqual(len(table), 1)
        b = table[0]
        self.assertAlmostEqual(b.half[0], 1.0)
        self.assertAlmostEqual(b.half[1], 0.5)
        self.assertFalse(b.round)
        self.assertLessEqual(b.y[0], 0.0)
        self.assertGreaterEqual(b.y[1], 0.7)
        rug = S.blockers_of("r", "rug", box_faces(-1.5, 0.0, -1.0, 1.5, 0.02, 1.0), (0, 0, 0), 0.0, f)
        self.assertEqual(rug, [], "below the step height")
        banner = S.blockers_of("b", "banner", box_faces(-0.5, 2.5, -0.05, 0.5, 6.0, 0.05), (0, 0, 0), 0.0, f)
        self.assertEqual(banner, [], "overhead")

    def test_round_parts_are_circles_and_turned_ones_oriented(self):
        f = floors()
        trunk = S.blockers_of("tree", "tree", cylinder_faces(3.0, 2.0, 0.3, -0.1, 3.0), (3.0, 0.0, 2.0), 0.7, f)
        self.assertEqual(len(trunk), 1)
        self.assertTrue(trunk[0].round)
        self.assertAlmostEqual(trunk[0].half[0], 0.3, delta=0.01)
        # A bench turned 30 degrees: an oriented box in the bench's own axes.
        yaw = math.radians(30)
        local = box_faces(-1.0, 0.0, -0.2, 1.0, 0.45, 0.2)
        s, c = math.sin(yaw), math.cos(yaw)
        world = [[(x * c + z * s + 5.0, y, -x * s + z * c + 5.0) for x, y, z in face] for face in local]
        bench = S.blockers_of("bench", "bench", world, (5.0, 0.0, 5.0), yaw, f)
        self.assertEqual(len(bench), 1)
        self.assertAlmostEqual(bench[0].yaw, yaw, places=4)
        self.assertAlmostEqual(bench[0].half[0], 1.0, places=3)
        self.assertTrue(bench[0].holds(5.0 + 0.9 * c, 5.0 - 0.9 * s))
        self.assertFalse(bench[0].holds(5.0 + 0.9 * s, 5.0 + 0.9 * c), "across it, 0.9 m out")
        # A quarter turn is an axis-aligned box with its halves swapped.
        quarter = [[(z + 8.0, y, -x + 8.0) for x, y, z in face] for face in local]
        b = S.blockers_of("q", "bench", quarter, (8.0, 0.0, 8.0), math.pi / 2, f)[0]
        self.assertEqual(b.yaw, 0.0)
        self.assertAlmostEqual(b.half[0], 0.2, places=3)
        self.assertAlmostEqual(b.half[1], 1.0, places=3)

    def test_parts_closer_than_a_character_join_and_others_stay_apart(self):
        f = floors()
        faces = box_faces(0.0, 0.0, 0.0, 0.4, 1.0, 0.4) + box_faces(0.7, 0.0, 0.0, 1.1, 1.0, 0.4)
        self.assertEqual(len(S.blockers_of("p", "p", faces, (0, 0, 0), 0.0, f)), 1, "0.3 m apart: one")
        faces = box_faces(0.0, 0.0, 0.0, 0.4, 1.0, 0.4) + box_faces(1.5, 0.0, 0.0, 1.9, 1.0, 0.4)
        self.assertEqual(len(S.blockers_of("p", "p", faces, (0, 0, 0), 0.0, f)), 2, "1.1 m apart: two")

    def test_the_outline_rule_is_sim_cores(self):
        # sim_core::layout::Blocker::local / holds: local x = dx cos - dz sin, local z = dx sin + dz cos.
        b = S.Blocker("b", (1.0, 2.0), (1.0, 0.25), (0.0, 1.0), yaw=math.pi / 6)
        lx, lz = b.local(1.0 + math.cos(math.pi / 6), 2.0 - math.sin(math.pi / 6))
        self.assertAlmostEqual(lx, 1.0, places=5)
        self.assertAlmostEqual(lz, 0.0, places=5)
        self.assertFalse(b.holds(1.0 + math.cos(math.pi / 6), 2.0 - math.sin(math.pi / 6)), "on its end: not inside")
        r = S.Blocker("r", (0.0, 0.0), (1.0, 1.0), (0.0, 1.0), round=True)
        self.assertTrue(r.holds(0.69, 0.69))
        self.assertFalse(r.holds(0.72, 0.72), "outside the circle, inside its box")


class ShippedTests(unittest.TestCase):
    """The shipped layouts' solids."""

    def test_the_layouts_solids_are_current(self):
        for lid, (blockers, _) in generated().items():
            self.assertEqual(S.check_ids(blockers), [], lid)
            with open(S.layout_path(lid), encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(S.patch(text, blockers), text, f"{lid}: run world/solids.py --write")

    def test_the_owl_exchange_counter_and_the_great_hall_are_solid(self):
        castle = by_id("castle")
        counter = castle["entrance_hall/owl_counter"]
        self.assertTrue(counter.holds(1.3, 30.0), "the counter's middle")
        self.assertGreater(counter.half[1], 5.0, "its whole length")
        for t in ("great_hall/table_1", "great_hall/table_2", "great_hall/bench_1_n", "great_hall/staff_table"):
            self.assertIn(t, castle)
        self.assertTrue(castle["great_hall/table_1"].holds(60.0, 11.8))

    def test_fountains_wells_and_trees_block_by_their_rim_and_trunk(self):
        castle = by_id("castle")
        fountain = castle["courtyard/fountain"]
        self.assertTrue(fountain.round)
        self.assertGreater(fountain.half[0], 3.5, "the basin's rim, not just the statue")
        self.assertTrue(castle["clock_courtyard/well"].round)
        trees = [b for b in castle.values() if b.piece.startswith("kit_tree_")]
        self.assertGreater(len(trees), 30)
        for t in trees:
            self.assertLess(max(t.half), 0.6, f"{t.id}: a trunk, not the crown")
            self.assertLess(t.y[1] - t.y[0], 3.0, f"{t.id}: up to head height")

    def test_doors_rugs_overhead_and_tabletop_pieces_are_never_solid(self):
        never = ("kit_door_", "kit_rug_", "kit_banner_", "kit_chandelier", "kit_table_setting_", "kit_stone_arch_",
                 "kit_floor_", "kit_lantern_", "classroom_door", "tell_")
        for lid, (blockers, _) in generated().items():
            for b in blockers:
                self.assertFalse(b.piece.startswith(never), f"{lid}: {b.id} ({b.piece})")
                self.assertFalse(b.id.split("/")[-1].startswith("entrance_"), f"{lid}: {b.id} is an entrance")

    def test_seats_doorways_and_arrival_points_stay_usable(self):
        for lid, (_, report) in generated().items():
            stuck = [r for r in report if "no standing spot" in r]
            self.assertEqual(stuck, [], lid)
            layout = L.load_layout(S.layout_path(lid))
            blockers = generated()[lid][0]
            for sp in layout.spawns:
                x, y, z = sp["pos"]
                self.assertFalse(any(b.blocks(x, y, z) for b in blockers), f"{lid}: spawn {sp['id']}")
            for it in layout.interactables:
                if it.kind in S.ARRIVALS:
                    x, y, z = it.pos
                    self.assertFalse(any(b.y[0] - 0.5 <= y <= b.y[1] and b.holds(x, z, S.RADIUS) for b in blockers),
                                     f"{lid}: {it.id}")

    def test_every_class_seat_can_be_sat_on(self):
        # The rooms' seat rows each keep a standing spot within the client's sitting reach.
        for lid in list(S.kit_rooms()) + list(S.mesh_rooms()):
            if lid not in generated():
                continue
            report = generated()[lid][1]
            self.assertFalse([r for r in report if "no standing spot" in r], lid)


if __name__ == "__main__":
    unittest.main()
