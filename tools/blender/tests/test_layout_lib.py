"""Unit tests for layout_lib (pure Python; run through tests/run_tests.py)."""

import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.dirname(HERE))

import layout_lib as L  # noqa: E402

LAYOUT_PATH = os.path.join(REPO, "data", "world", "layout.json")
PROPS_PATH = os.path.join(REPO, "data", "world", "props.json")


def surf(sid, x0, z0, x1, z1, y=0.0, kind="room", ceiling=5.0, **kw):
    d = {"id": sid, "kind": kind, "name": sid, "level": kw.pop("level", 0), "cell": kw.pop("cell", "c"),
         "min": [x0, z0], "max": [x1, z1], "y": y}
    if ceiling is not None:
        d["ceiling"] = ceiling
    d.update(kw)
    return d


def layout(surfaces, spawn=(1.0, 0.0, 1.0)):
    return L.parse_layout({
        "version": 1,
        "surfaces": surfaces,
        "spawns": [{"id": "s", "pos": list(spawn), "yaw": 0.0}],
    })


def two_rooms():
    """Two 10 x 10 rooms 1 m apart, joined by a 3 m door 4 m high."""
    return layout([
        surf("a", 0, 0, 10, 10, ceiling=8.0),
        surf("door", 10, 4, 11, 7, kind="door", ceiling=4.0),
        surf("b", 11, 0, 21, 10, ceiling=6.0),
    ])


class HeightTests(unittest.TestCase):
    def test_stairs_rise_toward_their_direction(self):
        cases = {"+x": ((0, 2), (4, 2)), "-x": ((4, 2), (0, 2)), "+z": ((2, 0), (2, 4)), "-z": ((2, 4), (2, 0))}
        for rise, (low, high) in cases.items():
            lay = layout([surf("st", 0, 0, 4, 4, y=1.0, kind="stair", y_top=3.0, rise=rise)], spawn=(low[0], 1.0, low[1]))
            s = lay.surfaces[0]
            self.assertAlmostEqual(s.height(*low), 1.0, msg=rise)
            self.assertAlmostEqual(s.height(*high), 3.0, msg=rise)
            self.assertAlmostEqual(s.height(2, 2), 2.0, msg=rise)

    def test_flat_surfaces_ignore_position(self):
        s = layout([surf("f", 0, 0, 4, 4, y=2.5)], spawn=(1, 2.5, 1)).surfaces[0]
        self.assertEqual(s.height(0, 0), 2.5)
        self.assertEqual(s.height(4, 4), 2.5)


class ParseTests(unittest.TestCase):
    def test_rejects_unknown_kind_and_fields(self):
        with self.assertRaises(L.LayoutError):
            layout([surf("x", 0, 0, 1, 1, kind="lava")])
        with self.assertRaises(L.LayoutError):
            layout([surf("x", 0, 0, 1, 1, colour="red")])

    def test_rejects_bad_rise(self):
        with self.assertRaises(L.LayoutError):
            layout([surf("x", 0, 0, 1, 1, kind="stair", y_top=1.0, rise="up")])


class ConnectivityTests(unittest.TestCase):
    def test_shared_edge_at_same_height_connects(self):
        lay = two_rooms()
        a, door, b = lay.surfaces
        self.assertTrue(L.connected(a, door))
        self.assertTrue(L.connected(door, b))
        self.assertFalse(L.connected(a, b))  # 1 m apart

    def test_corner_touch_does_not_connect(self):
        lay = layout([surf("a", 0, 0, 1, 1), surf("b", 1, 1, 2, 2)])
        self.assertIsNone(L.contact(*lay.surfaces))
        self.assertFalse(L.connected(*lay.surfaces))

    def test_height_difference_beyond_step_blocks(self):
        lay = layout([surf("a", 0, 0, 1, 1), surf("b", 1, 0, 2, 1, y=0.36)])
        self.assertFalse(L.connected(*lay.surfaces))
        lay = layout([surf("a", 0, 0, 1, 1), surf("b", 1, 0, 2, 1, y=0.34)])
        self.assertTrue(L.connected(*lay.surfaces))

    def test_stair_connects_at_both_ends_only(self):
        lay = layout([
            surf("low", 0, 0, 4, 2),
            surf("st", 0, 2, 4, 10, kind="stair", y_top=4.0, rise="+z", ceiling=8.0),
            surf("high", 0, 10, 4, 12, y=4.0),
            surf("side", 4, 2, 6, 10),  # floor beside the stair
        ])
        low, st, high, side = lay.surfaces
        self.assertTrue(L.connected(low, st))
        self.assertTrue(L.connected(st, high))
        # Beside the stair only its first ~0.7 m is within a step of the floor.
        self.assertTrue(L.connected(st, side))
        self.assertFalse(L.connected(low, high))

    def test_stacked_floors_do_not_connect(self):
        lay = layout([surf("down", 0, 0, 10, 10, ceiling=5.4), surf("up", 0, 0, 10, 10, y=6.0, level=1)])
        self.assertFalse(L.connected(*lay.surfaces))

    def test_validate_reports_unreachable_surfaces(self):
        lay = layout([surf("a", 0, 0, 10, 10), surf("island", 20, 0, 30, 10)])
        rep = L.validate(lay)
        self.assertFalse(rep.ok)
        self.assertTrue(any("island" in e and "unreachable" in e for e in rep.errors), rep.errors)

    def test_validate_reports_low_headroom_overlap(self):
        lay = layout([surf("a", 0, 0, 10, 10), surf("shelf", 2, 2, 4, 4, y=1.5)])
        self.assertTrue(any("overlap" in e for e in L.validate(lay).errors))

    def test_validate_reports_low_overhead_clearance(self):
        # A flight under a landing: 2 m of walking headroom passes the stacking
        # rule, but the landing's 0.4 m slab leaves a tall character 1.6 m.
        hall = surf("hall", 0, 0, 20, 2, ceiling=20.0)
        flight = surf("flight", 2, 2, 18, 6, kind="stair", y=0.0, y_top=9.0, rise="-x", ceiling=20.0)
        low = layout([hall, flight, surf("landing", 6, 2, 10, 6, kind="corridor", y=9.0, level=1, ceiling=8.0)])
        errs = L.validate(low).errors
        self.assertFalse(any("overlap with only" in e for e in errs), errs)
        self.assertTrue(any("under the one above" in e and "landing" in e for e in errs), errs)
        # The same landing over the flight's low end clears it.
        high = layout([hall, flight, surf("landing", 13, 2, 17, 6, kind="corridor", y=9.0, level=1, ceiling=8.0)])
        self.assertFalse(any("under the one above" in e for e in L.validate(high).errors))

    def test_validate_reports_narrow_doors(self):
        lay = layout([
            surf("a", 0, 0, 10, 10),
            surf("door", 10, 4, 11, 4.5, kind="door", ceiling=3.0),
            surf("b", 11, 0, 21, 10),
        ])
        self.assertTrue(any("wide" in e for e in L.validate(lay).errors))

    def test_validate_reports_roof_through_floor_above(self):
        lay = layout([surf("down", 0, 0, 10, 10, ceiling=5.8), surf("up", 0, 0, 10, 10, y=6.0, level=1)])
        self.assertTrue(any("slab" in e for e in L.validate(lay).errors))


class GeometryHelperTests(unittest.TestCase):
    def test_rect_subtract_preserves_area(self):
        rect = (0, 0, 10, 10)
        holes = [(2, 2, 4, 4), (3, 3, 6, 5), (8, -1, 12, 3)]
        parts = L.rect_subtract(rect, holes)
        area = sum((r[2] - r[0]) * (r[3] - r[1]) for r in parts)
        # 100 - (4 + 6 - 1) - (2 x 3 clipped to the rectangle) = 85
        self.assertAlmostEqual(area, 85.0)
        for r in parts:
            for h in holes:
                ox = min(r[2], h[2]) - max(r[0], h[0])
                oz = min(r[3], h[3]) - max(r[1], h[1])
                self.assertFalse(ox > 1e-9 and oz > 1e-9)

    def test_interval_subtract_cuts_openings(self):
        lin = lambda v: L.Lin(v, v)  # noqa: E731
        wall = [(lin(0), lin(8))]
        out = L.iv_subtract(wall, [(lin(0), lin(4))])
        self.assertEqual([(lo.mid, hi.mid) for lo, hi in out], [(4, 8)])
        out = L.iv_subtract(wall, [(lin(2), lin(3)), (lin(5), L.LIN_INF)])
        self.assertEqual([(lo.mid, hi.mid) for lo, hi in out], [(0, 2), (3, 5)])

    def test_oriented_faces_requested_normal(self):
        poly = L.oriented([(0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)], (0, 1, 0))
        self.assertGreater(L.newell_normal(poly)[1], 0)


def wall_faces(plan, cell="c"):
    return plan.polys.get((cell, "wall"), [])


class PlannerTests(unittest.TestCase):
    def test_door_cuts_opening_and_leaves_header(self):
        plan = L.plan_layout(two_rooms())
        # Room a's east wall (x = 10, facing -x): full height except the door.
        east = [p for p in wall_faces(plan) if all(abs(v[0] - 10) < 1e-6 for v in p)]
        covered_at = lambda y, z: any(  # noqa: E731
            min(v[1] for v in p) - 1e-6 <= y <= max(v[1] for v in p) + 1e-6
            and min(v[2] for v in p) - 1e-6 <= z <= max(v[2] for v in p) + 1e-6
            for p in east
        )
        self.assertTrue(covered_at(2.0, 2.0))  # beside the door
        self.assertFalse(covered_at(2.0, 5.5))  # the opening
        self.assertTrue(covered_at(6.0, 5.5))  # header above it
        self.assertEqual(L.find_z_fighting(plan), [])

    def test_landing_over_a_hall_gets_railing_and_underside(self):
        # The hall floor is tiled around the stair so nothing walkable is under it.
        lay = layout([
            surf("hall_w", 0, 0, 8, 20, ceiling=14.0),
            surf("hall_e", 12, 0, 20, 20, ceiling=14.0),
            surf("hall_s", 8, 16, 12, 20, ceiling=14.0),
            surf("landing", 8, 0, 12, 4, y=6.0, ceiling=8.0, level=1),
            surf("st", 8, 4, 12, 16, kind="stair", y_top=6.0, rise="-z", ceiling=14.0),
        ], spawn=(1, 0, 10))
        self.assertTrue(L.validate(lay).ok, L.validate(lay).errors)
        plan = L.plan_layout(lay)
        self.assertTrue(plan.polys.get(("c", "railing")))
        self.assertEqual(L.find_z_fighting(plan), [])
        self.assertEqual(L.degenerate_polygons(plan), [])

    def test_stairs_get_one_tread_per_riser(self):
        lay = layout([surf("low", 0, 0, 4, 2), surf("st", 0, 2, 4, 10, kind="stair", y_top=3.2, rise="+z")])
        plan = L.plan_layout(lay)
        treads = [p for p in plan.polys[("c", "floor_stair")] if L.newell_normal(p)[1] > 0]
        self.assertEqual(len(treads), round(3.2 / L.STEP_RISER))
        # Tread heights straddle the ramp: never more than half a riser off.
        st = lay.surfaces[1]
        r = 3.2 / len(treads)
        for t in treads:
            cz = sum(v[2] for v in t) / len(t)
            self.assertLessEqual(abs(t[0][1] - st.height(2, cz)), r / 2 + 1e-6)

    def test_shared_ceilings_are_not_doubled(self):
        lay = layout([surf("a", 0, 0, 10, 10, ceiling=8.0), surf("b", 2, 2, 4, 4, y=4.0, ceiling=4.0)], spawn=(1, 0, 1))
        plan = L.plan_layout(lay)
        self.assertEqual(L.find_z_fighting(plan), [])


def gated_surfaces(house="skytalon"):
    return [
        surf("hall", 0, 0, 10, 10),
        surf("door", 10, 4, 11, 7, kind="door", ceiling=3.0, house=house),
        surf("common", 11, 0, 21, 10, house=house),
    ]


def gated_layout(**over):
    """A hall, a doorway and a common room gated to one house, with its dorm door."""
    house = over.pop("house", "skytalon")
    data = {
        "version": 1,
        "surfaces": gated_surfaces(house),
        "spawns": [{"id": "s", "pos": [5.0, 0.0, 5.0], "yaw": 0.0}],
        "interactables": [{"id": "dorm_door_x", "kind": "dorm_door", "house": house, "pos": [18.0, 0.0, 5.0],
                           "yaw": 1.5, "radius": 1.5}],
    }
    data.update(over)
    return L.parse_layout(data)


class GateTests(unittest.TestCase):
    def test_valid_gate_and_house_views(self):
        lay = gated_layout()
        self.assertEqual(L.validate(lay).errors, [])
        own = L.for_house(lay, "skytalon")
        other = L.for_house(lay, "serpentyne")
        self.assertEqual([s.id for s in own.surfaces], ["hall", "door", "common"])
        self.assertEqual([s.id for s in other.surfaces], ["hall"])
        self.assertEqual(len(other.interactables), 0)

    def test_rejects_unknown_houses_and_mixed_gates(self):
        self.assertTrue(any("unknown house" in e for e in L.validate(gated_layout(house="no_such_house")).errors))
        rival = gated_surfaces() + [surf("rival", 21, 0, 25, 10, house="serpentyne")]
        self.assertTrue(any("must not connect" in e for e in L.validate(gated_layout(surfaces=rival)).errors))

    def test_spawn_behind_a_gate_and_cut_off_public_space(self):
        lay = gated_layout(spawns=[{"id": "s", "pos": [15.0, 0.0, 5.0], "yaw": 0.0}])
        self.assertTrue(any("behind a house gate" in e for e in L.validate(lay).errors))
        # A public room reachable only through the common room.
        beyond = gated_surfaces() + [surf("beyond", 21, 0, 30, 10)]
        self.assertTrue(any("only through a house gate" in e for e in L.validate(gated_layout(surfaces=beyond)).errors))

    def test_interactable_rules(self):
        lay = gated_layout(interactables=[{"id": "d", "kind": "dorm_door", "pos": [18.0, 0.0, 5.0], "radius": 1.5}])
        self.assertTrue(any("needs a house" in e for e in L.validate(lay).errors))
        lay = gated_layout(interactables=[{"id": "x", "kind": "dorm_exit", "pos": [18.0, 0.0, 5.0], "radius": 1.5}])
        self.assertTrue(any("not on ground its users" in e for e in L.validate(lay).errors))
        lay = gated_layout(interactables=[{"id": "d", "kind": "dorm_door", "house": "skytalon", "pos": [18.0, 0.0, 5.0],
                                           "radius": 0.0}])
        self.assertTrue(any("bad radius" in e for e in L.validate(lay).errors))
        with self.assertRaises(L.LayoutError):
            gated_layout(interactables=[{"id": "d", "kind": "teleporter", "pos": [1, 0, 1], "radius": 1}])
        it = gated_layout().interactables[0]
        self.assertTrue(it.in_reach((17.0, 0.0, 5.5)))
        self.assertFalse(it.in_reach((18.0, 3.0, 5.0)))

    def test_place_area_rules(self):
        ok = [{"id": "floor", "surface": "floor", "min": [0, 0, 0], "max": [4, 0, 4]},
              {"id": "wall", "surface": "wall", "min": [0, 0.3, 0], "max": [4, 3, 0], "facing": 0.0}]
        self.assertEqual(L.validate(gated_layout(place_areas=ok)).errors, [])
        bad = [{"id": "w", "surface": "wall", "min": [0, 0.3, 0], "max": [4, 3, 0], "facing": 1.5707963},
               {"id": "f", "surface": "floor", "min": [0, 0, 0], "max": [4, 1, 4]},
               {"id": "g", "surface": "wall", "min": [0, 0.3, 0], "max": [4, 3, 0]}]
        errors = L.validate(gated_layout(place_areas=bad)).errors
        for what in ("perpendicular", "horizontal", "need a facing"):
            self.assertTrue(any(what in e for e in errors), (what, errors))

    def test_thin_walls_are_rejected(self):
        lay = layout([surf("a", 0, 0, 10, 10), surf("b", 10.05, 0, 20, 10)])
        self.assertTrue(any("wall needs more than" in e for e in L.validate(lay).errors))


class PropTests(unittest.TestCase):
    def write(self, data):
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        self.addCleanup(os.remove, path)
        return path

    def test_repeat_shapes_and_lights(self):
        path = self.write({"props": [
            {"id": "barrel", "cell": "c", "material": "prop_wood", "shape": "cylinder_x", "min": [0, 0, 0],
             "max": [1, 0.9, 0.9], "repeat": {"count": 3, "step": [0, 0, 1]}}],
            "lights": [{"id": "l", "cell": "c", "pos": [0, 2, 0], "color": "#FFC877", "intensity": 3, "range": 6}]})
        props = L.load_props(path)
        self.assertEqual([p.id for p in props], ["barrel_0", "barrel_1", "barrel_2"])
        self.assertEqual(props[2].min[2], 2.0)
        self.assertEqual(L.load_lights(path)[0].to_json()["kind"], "point")
        faces = L.cylinder_faces(props[0].min, props[0].max, "x")
        self.assertEqual(len(faces), L.CYLINDER_SEGMENTS + 2)
        # Every side faces away from the axis.
        for f in faces[:-2]:
            n = L.newell_normal(f)
            c = [sum(p[k] for p in f) / len(f) for k in range(3)]
            self.assertGreater(n[1] * (c[1] - 0.45) + n[2] * (c[2] - 0.45), 0)

    def test_bad_props_and_lights(self):
        with self.assertRaises(L.LayoutError):
            L.load_props(self.write({"props": [{"id": "x", "cell": "c", "material": "prop_wood", "shape": "sphere",
                                                "min": [0, 0, 0], "max": [1, 1, 1]}]}))
        with self.assertRaises(L.LayoutError):
            L.load_lights(self.write({"lights": [{"id": "l", "cell": "c", "pos": [0, 0, 0], "color": "orange",
                                                  "intensity": 1, "range": 1}]}))

    def test_hanging_props_keep_their_underside(self):
        lay = layout([surf("room", 0, 0, 10, 10)])
        props = [L.Prop("stool", "c", "prop_wood", (1, 0, 1), (2, 0.5, 2)),
                 L.Prop("lamp", "c", "prop_wood", (4, 3, 4), (5, 3.5, 5))]
        plan = L.plan_layout(lay, props)
        down = [p for p in plan.polys[("c", "prop_wood")] if L.newell_normal(p)[1] < 0]
        self.assertEqual(len(down), 1)  # the lamp's, not the stool's


class RealLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = L.load_layout(LAYOUT_PATH)
        cls.props = L.load_props(PROPS_PATH)
        cls.plan = L.plan_layout(cls.layout, cls.props)

    def test_layout_is_valid_and_fully_reachable(self):
        rep = L.validate(self.layout)
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.warnings, [])

    def test_required_milestone_spaces_exist(self):
        names = {s.name for s in self.layout.surfaces}
        for required in ("Entrance Hall", "Great Hall", "Marble Staircase", "Front Doors",
                         "Entrance Courtyard", "First-Floor North Corridor", "Ground-Floor West Corridor"):
            self.assertIn(required, names)
        stair_levels = {s.level for s in self.layout.surfaces if s.cell == "grand_staircase"}
        self.assertTrue({0, 1, 2, 3} <= stair_levels)
        self.assertIn("owl_exchange", {lm["id"] for lm in self.layout.landmarks})

    def test_every_surface_has_a_cell_and_cells_are_small(self):
        for s in self.layout.surfaces:
            self.assertRegex(s.cell, L.CELL_ID_RE)
        for cell in self.layout.cells():
            lo, hi = self.plan.bounds(cell)
            self.assertLessEqual(max(hi[0] - lo[0], hi[2] - lo[2]), 90, cell)

    def test_plan_is_clean(self):
        self.assertEqual(L.degenerate_polygons(self.plan), [])
        self.assertEqual(L.find_z_fighting(self.plan), [])

    def test_draw_call_and_triangle_budget(self):
        # One mesh per (cell, material); keep the whole slice far below budget
        # and every cell within the style guide's 40 draw calls.
        self.assertLess(len(self.plan.polys), 400)  # 63 cells (318 meshes)
        self.assertLess(self.plan.triangle_count(), 50_000)
        per_cell = {}
        for cell, _ in self.plan.polys:
            per_cell[cell] = per_cell.get(cell, 0) + 1
        self.assertLessEqual(max(per_cell.values()), 40, per_cell)

    def test_grand_staircase_reaches_the_seventh_floor(self):
        levels = {s.level for s in self.layout.surfaces if s.id.startswith("gs_")}
        self.assertTrue(set(range(8)) <= levels, levels)
        # The seventh-floor galleries are level with the seventh-floor corridor.
        top = [s for s in self.layout.surfaces if s.id.startswith("gs_l7_")]
        self.assertTrue(top)
        self.assertEqual({s.y for s in top}, {self.layout.by_id("l7_corridor_north").y})

    def test_common_rooms_are_gated_with_a_dorm_door_each(self):
        houses = L.known_houses()
        self.assertEqual(sorted(self.layout.gated()), sorted(houses))
        doors = {i.house: i for i in self.layout.interactables if i.kind == "dorm_door"}
        self.assertEqual(sorted(doors), sorted(houses))
        for h in houses:
            view = L.for_house(self.layout, h)
            starts = [L.spawn_surface(view, sp["pos"]).id for sp in view.spawns]
            seen = L.reachable(view, starts)
            door_surface = L.interactable_surface(view, doors[h])
            self.assertIn(door_surface.id, seen, h)
            self.assertEqual(door_surface.house, h)
            # The room is a cell of its own, and no other house can stand in it.
            self.assertTrue(door_surface.cell.startswith("common_room_house_"))
            for other in houses:
                if other != h:
                    self.assertNotIn(door_surface.id, {s.id for s in L.for_house(self.layout, other).surfaces})

    def test_house_entrance_placement(self):
        by_id = {s.id: s for s in self.layout.surfaces}
        doors = {s.house: s for s in self.layout.surfaces if s.house and s.kind == "door"}
        self.assertEqual(doors["roarwing"].level, 7)  # behind a portrait on the seventh floor
        self.assertGreaterEqual(doors["skytalon"].floor_min, 30)  # top of the west tower
        self.assertLess(doors["serpentyne"].floor_min, 0)  # dungeons
        self.assertLess(doors["burrowsby"].floor_min, 0)  # basement by the kitchens
        self.assertIn("eh_dungeon_steps", by_id)
        self.assertIn("eh_kitchen_steps", by_id)
        # The west tower stair winds round a solid core: many short flights.
        flights = [s for s in self.layout.surfaces if s.id.startswith("wt_") and s.kind == "stair"]
        self.assertGreaterEqual(len(flights), 12)

    def test_cell_lights_reference_cells(self):
        lights = L.load_lights(PROPS_PATH)
        cells = set(self.plan.cells())
        self.assertTrue(lights)
        for lt in lights:
            self.assertIn(lt.cell, cells, lt.id)

    def test_file_round_trips_through_json(self):
        with open(LAYOUT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(len(L.parse_layout(copy.deepcopy(data)).surfaces), len(data["surfaces"]))


# =====================================================================================
# Castle M5c (Facades): the outer-shell cut (docs/art/castle-kit.md "Exterior").
# =====================================================================================


def _rects(polys, axis, c, sign):
    """(t0, y0, t1, y1) of the flat rectangles on the plane axis = c facing sign."""
    out = []
    for poly in polys:
        r = L._flat_rect(poly, axis)
        if r is not None and abs(r[4] - c) < 1e-6 and r[5] == sign:
            out.append(tuple(round(v, 4) for v in r[:4]))
    return sorted(out)


def _covers(rects, t, y):
    return any(t0 < t < t1 and y0 < y < y1 for t0, y0, t1, y1 in rects)


class M5cOuterShellTests(unittest.TestCase):
    """The traceried window over the front doors is cut through the wall mass
    the hall shares with the courtyard (the Planner with holes, the kitted
    export's plan1): the hall's outer face above the courtyard's wall top, the
    courtyard's own wall face and wall-top cap below and at it, the reveal
    closed across the mass; without holes (plan0, the greybox) nothing
    changes, and the cut touches no other cell."""

    HOLE = L.WallHole("eh_floor_south", "z", 40.0, -1.0, 12.0, 20.0, 10.8, 18.8, True)

    @classmethod
    def setUpClass(cls):
        cls.layout = L.load_layout(LAYOUT_PATH)
        cls.plan0 = L.plan_layout(cls.layout, [])
        cls.plan1 = L.plan_layout(cls.layout, [], (), [cls.HOLE])
        real = L.outer_cut_m5c
        try:
            L.outer_cut_m5c = lambda pl: None
            cls.uncut = L.plan_layout(cls.layout, [], (), [cls.HOLE])
        finally:
            L.outer_cut_m5c = real

    def test_cut_through_the_shared_mass(self):
        hall = _rects(self.plan1.polys[("entrance_hall", "wall_exterior")], "z", 40.8, 1.0)
        # The hall's outer face (z 40.8) above the courtyard's 16 m wall: the head's rectangle open.
        self.assertFalse(_covers(hall, 16.0, 17.0))
        self.assertFalse(_covers(hall, 19.9, 18.7))
        self.assertTrue(_covers(hall, 16.0, 19.5))  # above the hole's head
        self.assertTrue(_covers(hall, 10.0, 17.0))  # beside it
        before = _rects(self.uncut.polys[("entrance_hall", "wall_exterior")], "z", 40.8, 1.0)
        self.assertTrue(_covers(before, 16.0, 17.0))
        # The courtyard's own wall face (z 42, facing it) open from the sill to its top.
        court = _rects(self.plan1.polys[("courtyard", "wall")], "z", 42.0, 1.0)
        for y in (11.0, 13.4, 15.9):
            self.assertFalse(_covers(court, 16.0, y), y)
        self.assertTrue(_covers(court, 16.0, 10.4))  # the wall over the front doors, under the sill
        self.assertTrue(_covers(court, 22.0, 13.0))
        # Its wall-top cap (y 16, z 41..42) open over the hole.
        caps = [p for p in self.plan1.polys[("courtyard", "wall_exterior")] if all(abs(q[1] - 16.0) < 1e-6 for q in p)]
        over = [p for p in caps if min(q[0] for q in p) < 16.0 < max(q[0] for q in p)
                and min(q[2] for q in p) < 41.5 < max(q[2] for q in p)]
        self.assertEqual(over, [])

    def test_reveal_closed_across_the_mass(self):
        added = [p for p in self.plan1.polys[("entrance_hall", "wall_exterior")]
                 if p not in self.uncut.polys[("entrance_hall", "wall_exterior")]]
        jambs = [p for p in added if all(abs(q[0] - p[0][0]) < 1e-9 for q in p)]
        self.assertEqual(sorted(round(p[0][0], 4) for p in jambs), [12.0, 20.0])
        for p in jambs:
            n = L.newell_normal(p)
            self.assertGreater(n[0] * (16.0 - p[0][0]), 0)  # facing into the hole
            self.assertEqual((round(min(q[1] for q in p), 4), round(max(q[1] for q in p), 4)), (10.8, 16.0))
            self.assertEqual((round(min(q[2] for q in p), 4), round(max(q[2] for q in p), 4)), (40.8, 42.0))
        sills = [p for p in added if all(abs(q[1] - 10.8) < 1e-9 for q in p)]
        self.assertEqual(len(sills), 1)
        self.assertGreater(L.newell_normal(sills[0])[1], 0)
        # No head under the wall top: the window's head rises above the courtyard's 16 m wall.
        self.assertFalse([p for p in added if all(abs(q[1] - 18.8) < 1e-9 for q in p)])

    def test_only_the_hall_and_the_courtyard_change(self):
        changed = sorted(k for k in set(self.plan1.polys) | set(self.uncut.polys)
                         if self.plan1.polys.get(k) != self.uncut.polys.get(k))
        self.assertEqual(changed, [("courtyard", "wall"), ("courtyard", "wall_exterior"), ("entrance_hall", "wall_exterior")])
        # Without holes (the greybox, the colliders) the plan is the M1.0 one.
        again = L.plan_layout(self.layout, [])
        self.assertEqual(again.polys, self.plan0.polys)

    def test_rules_of_the_cut(self):
        """A window not over a door, or on a wall not shared with an outdoor
        surface, keeps the old behaviour (no cut through any mass)."""
        lay = layout([
            surf("room", 0, 0, 20, 20, ceiling=20.0, cell="hall"),
            surf("door", 6, 20, 14, 22, kind="door", ceiling=10.0, cell="hall"),
            surf("yard", -10, 22, 30, 50, kind="courtyard", ceiling=None, cell="yard"),
        ])
        over = L.WallHole("room", "z", 20.0, -1.0, 6.0, 14.0, 10.8, 18.8, True)
        aside = L.WallHole("room", "z", 20.0, -1.0, 15.0, 18.0, 4.0, 8.0, True)
        p = L.plan_layout(lay, [], (), [over, aside])
        yard = _rects(p.polys[("yard", "wall")], "z", 22.0, 1.0)
        self.assertFalse(_covers(yard, 10.0, 12.0))  # the window over the door: cut
        self.assertTrue(_covers(yard, 16.5, 6.0))  # the other one (no door under it): the yard's wall stays whole
        hall = _rects(p.polys[("hall", "wall_exterior")], "z", 20.8, 1.0)
        self.assertFalse(_covers(hall, 10.0, 17.0))
        lone = L.plan_layout(lay, [], (), [aside])
        self.assertEqual(lone.polys[("yard", "wall")], L.plan_layout(lay, []).polys[("yard", "wall")])


if __name__ == "__main__":
    unittest.main()
