"""Two-sided seats (world/seat_sides.py; docs/architecture.md):
which seats a sitter may also sit on facing the other
way, on made-up rooms and on the real castle, pitch and rooms."""

import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.path.dirname(HERE)
for _p in (BLENDER, os.path.join(BLENDER, "world")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import seat_sides as SS  # noqa: E402
import solids as S  # noqa: E402

BENCH = [-1.0, -0.2, 1.0, 0.2, 0.45, 0.0, 0.2, 0.2, 0.45]  # a 2 m bench at the origin, facing +z


def room(surfaces, solids=()):
    """Sides over a made-up layout: `surfaces` as (id, x0, z0, x1, z1, y)."""
    data = {"version": 1, "surfaces": [
        {"id": sid, "kind": "room", "name": "Room", "level": 0, "cell": "room", "min": [x0, z0], "max": [x1, z1], "y": y}
        for sid, x0, z0, x1, z1, y in surfaces], "solids": list(solids)}
    return SS.Sides.from_data(data)


def solid(bid, x, z, hx, hz, y0=-0.3, y1=0.8):
    return {"id": bid, "at": [x, z], "half": [hx, hz], "y": [y0, y1]}


class MadeUpTests(unittest.TestCase):

    def test_a_free_standing_bench_is_two_sided_even_round_its_own_blocker(self):
        sides = room([("floor", -5, -5, 5, 5, 0.0)], [solid("bench", 0.0, 0.0, 1.0, 0.2, -0.3, 0.45)])
        self.assertTrue(sides.two_sided(BENCH))
        turned = [-0.2, -1.0, 0.2, 1.0, 0.45, math.pi / 2, 0.2, 0.2, 0.45]
        self.assertTrue(sides.two_sided(turned))
        self.assertEqual(sides.mark([BENCH])[0][9], 0.45, "the legs rest on the same floor")

    def test_a_wall_behind_makes_it_one_way(self):
        # The floor stops 0.3 m behind the bench (a wall there: the layouts leave walls as gaps).
        sides = room([("floor", -5, -0.5, 5, 5, 0.0)])
        self.assertIsNone(sides.flip_drop(BENCH))
        self.assertEqual(sides.mark([BENCH])[0][9], 0)

    def test_a_table_in_front_does_not_matter_only_the_legs_space_behind(self):
        # A Great Hall bench: the table's edge 0.15 m ahead of the bench's, the aisle behind.
        sides = room([("floor", -5, -5, 5, 5, 0.0)], [solid("table", 0.0, 0.85, 1.5, 0.5)])
        self.assertTrue(sides.two_sided(BENCH))
        # A post or a table in the legs' space behind makes it one-way...
        post = room([("floor", -5, -5, 5, 5, 0.0)], [solid("post", 0.0, -(0.2 + SS.SIDE_CLEAR - 0.05), 0.15, 0.15)])
        self.assertFalse(post.two_sided(BENCH))
        # ... one beyond it does not.
        clear = room([("floor", -5, -5, 5, 5, 0.0)], [solid("table", 0.0, -(0.2 + SS.SIDE_CLEAR + 0.6), 1.5, 0.5)])
        self.assertTrue(clear.two_sided(BENCH))
        # Something low on the floor, under the leg space (below its top less LEG_DEPTH), is no obstacle.
        low = room([("floor", -5, -5, 5, 5, 0.0)], [solid("kerb", 0.0, -0.5, 1.5, 0.2, -0.3, -0.1)])
        self.assertTrue(low.two_sided(BENCH))

    def test_a_tier_up_behind_is_in_the_way_and_a_drop_is_not(self):
        # The next tier rises 0.3 m 0.4 m behind the bench: its riser is where the shins go.
        up = room([("tier0", -5, -0.6, 5, 5, 0.0), ("tier1", -5, -5, 5, -0.6, 0.3)])
        self.assertIsNone(up.flip_drop(BENCH))
        # A step of a few centimetres is still the floor.
        flat = room([("tier0", -5, -0.6, 5, 5, 0.0), ("tier1", -5, -5, 5, -0.6, 0.05)])
        self.assertAlmostEqual(flat.flip_drop(BENCH), 0.4)
        # A stand's front: the deck ends 0.3 m behind, the ground 2 m below. The legs hang over it,
        # fitted to the highest floor under them (the deck's edge, within the band).
        edge = room([("deck", -5, -0.5, 5, 5, 2.0), ("ground", -5, -5, 5, -0.5, 0.0)])
        top = [r if k != 4 else 2.45 for k, r in enumerate(BENCH)]
        self.assertAlmostEqual(edge.flip_drop(top), 0.45)
        # The deck ends under the bench: nothing but the ground far below, a drop wider than FLIP_DROP_MAX.
        far = room([("deck", -5, -0.1, 5, 5, 3.0), ("ground", -5, -5, 5, -0.1, 0.0)])
        high = [r if k != 4 else 3.45 for k, r in enumerate(BENCH)]
        self.assertEqual(far.flip_drop(high), SS.FLIP_DROP_MAX)

    def test_a_backed_seat_is_one_way(self):
        sides = room([("floor", -5, -5, 5, 5, 0.0)])
        self.assertEqual(sides.mark([BENCH], backed=[BENCH])[0][9], 0)

    def test_backs_and_arms_from_the_pieces_own_faces(self):
        from castle_kit import pieces as P
        from castle_kit.pieces import hall_furniture as HF

        def pts(piece):
            return [p for f in piece.faces for p in f.pts]

        bench = HF.bench(4.0)
        self.assertFalse(SS.backed(pts(bench), bench.meta["seats"][0]))
        chair = HF.chair_high()
        self.assertTrue(SS.backed(pts(chair), chair.meta["seats"][0]))
        for name in ("kit_sofa_2.4", "kit_armchair", "kit_chair_study", "kit_chair_row_staff"):
            piece = P.build(name, **P.CATALOGUE[name])
            self.assertTrue(SS.backed(pts(piece), piece.meta["seats"][0]), name)
        rustic = P.build("kit_bench_rustic", **P.CATALOGUE["kit_bench_rustic"])
        for row in rustic.meta.get("seats", []):
            self.assertFalse(SS.backed(pts(rustic), row))

    def test_room_seats_say_two_sided(self):
        sides = room([("floor", -5, -5, 5, 5, 0.0)])
        got = sides.mark_room([{"pos": [0.0, 0.0, 0.0], "yaw": 0.0, "front": 0.2, "back": 0.2, "drop": 0.45}])
        self.assertIs(got[0]["two_sided"], True)
        self.assertAlmostEqual(got[0]["flip_drop"], 0.46)
        walled = room([("floor", -5, -0.3, 5, 5, 0.0)])
        self.assertIs(walled.mark_room([{"pos": [0.0, 0.0, 0.0], "yaw": 0.0}])[0]["two_sided"], False)


_REPORT = None


def report():
    global _REPORT
    if _REPORT is None:
        _REPORT = SS.report()
    return _REPORT


class WorldTests(unittest.TestCase):
    """The real seats, from the committed layouts' solids."""

    @classmethod
    def setUpClass(cls):
        cls.builder = S.castle_builder()
        cls.sides = SS.Sides.from_file(S.LAYOUT_PATH)
        cls.floors = S.Floors(cls.builder.layout)

    def rows(self, cell):
        rows, backs = SS.cell_rows(self.builder, cell, self.floors)
        return dict(zip(map(tuple, rows), (r[9] for r in self.sides.mark(rows, backs))))

    def test_great_hall_table_benches_turn_and_the_staff_chairs_do_not(self):
        flags = self.rows("great_hall")
        tables = [r for r in flags if r[2] - r[0] > 10.0]  # the house tables' long benches
        self.assertEqual(len(tables), 8)
        for r in tables:
            self.assertAlmostEqual(flags[r], 0.45, msg=r)  # the aisle behind: the same floor
        chairs = [r for r in flags if r[4] > 1.0]  # on the dais
        self.assertGreater(len(chairs), 10)
        for r in chairs:
            self.assertEqual(flags[r], 0, r)

    def test_courtyard_wall_benches_are_one_way(self):
        flags = self.rows("courtyard")
        walled = [r for r in flags if r[0] in (-17.5, 49.1)]  # against the east and west walls
        self.assertEqual(len(walled), 4)
        for r in walled:
            self.assertEqual(flags[r], 0, r)
        self.assertGreaterEqual(sum(1 for f in flags.values() if f > 0), 3)

    def test_common_rooms_have_no_two_sided_seats(self):
        got = report()
        for area in ("common_room_house_a", "common_room_house_b", "common_room_house_c", "common_room_house_d",
                     "class_classroom"):
            if area in got:
                self.assertEqual(got[area][0], 0, area)

    def test_the_pitch_stand_benches_are_two_sided(self):
        two, one = report()["pitch"]
        self.assertGreater(two, 0)
        self.assertEqual(one, 0)


if __name__ == "__main__":
    unittest.main()
