"""Unit tests for the art: the Charms classroom and the duel
arena (world/school_rooms.py, school_layouts.py), the Dueling Club and
Charms door dressing of the castle (world/kit_school.py, props.json) and
the stage edges drawn without railings. Pure Python; run through
tests/run_tests.py."""

import json
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.join(os.path.dirname(HERE), "world"), os.path.dirname(HERE)]

import kit  # noqa: E402
import kit_models as km  # noqa: E402
import layout_lib as L  # noqa: E402
import meshkit as mk  # noqa: E402
import school_layouts  # noqa: E402
import school_rooms as sr  # noqa: E402

LAYOUT = os.path.join(REPO, "data", "world", "layout.json")
PROPS = os.path.join(REPO, "data", "world", "props.json")
INSTANCES = os.path.join(REPO, "data", "world", "instances")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def surface_at(layout, x, y, z, tol=0.02):
    for s in layout.surfaces:
        if s.x0 - 1e-6 <= x <= s.x1 + 1e-6 and s.z0 - 1e-6 <= z <= s.z1 + 1e-6 and abs(s.height(x, z) - y) <= tol:
            return s
    return None


class SchoolRoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rooms = sr.build_all()
        cls.layouts = {lid: L.parse_layout(r.layout) for lid, r in cls.rooms.items()}

    def test_layout_files_are_generated_from_the_rooms(self):
        for lid, text in school_layouts.generate().items():
            with open(os.path.join(INSTANCES, f"{lid}.json"), encoding="utf-8") as f:
                self.assertEqual(f.read(), text, f"{lid}.json is out of date: run school_layouts.py --write")

    def test_layouts_are_valid_and_reachable(self):
        for lid, layout in self.layouts.items():
            rep = L.validate(layout)
            self.assertEqual(rep.errors, [], lid)

    def test_contract_ids_kept(self):
        charms = self.rooms["class_charms"].layout
        self.assertIn("class_floor", [s["id"] for s in charms["surfaces"]])
        self.assertEqual([s["id"] for s in charms["spawns"]], ["class_entry_a", "class_entry_b"])
        self.assertEqual([i["id"] for i in charms["interactables"]], ["class_exit"])
        self.assertEqual(charms["interactables"][0]["kind"], "instance_exit")
        self.assertEqual(self.rooms["duel_arena"].layout, sr.ARENA_LAYOUT)

    def test_spawns_stand_out_of_the_exits_reach(self):
        # A student arriving must not be one key press from leaving again.
        for lid, room in self.rooms.items():
            exits = [i for i in room.layout["interactables"] if i["kind"] == "instance_exit"]
            for s in room.layout["spawns"]:
                for e in exits:
                    d = ((s["pos"][0] - e["pos"][0]) ** 2 + (s["pos"][2] - e["pos"][2]) ** 2) ** 0.5
                    self.assertGreater(d, e["radius"], f"{lid}: {s['id']} is in reach of {e['id']}")

    def test_professor_stands_on_the_teaching_floor(self):
        prof = load(os.path.join(REPO, "data", "classes", "charms.json"))["professor"]
        s = surface_at(self.layouts["class_charms"], *prof["pos"])
        self.assertIsNotNone(s)
        self.assertEqual(s.id, "class_floor")

    def test_tiers_rise_in_steps_toward_the_gallery(self):
        layout = self.layouts["class_charms"]
        heights = sorted({round(s.y, 3) for s in layout.surfaces})
        self.assertEqual(heights, [0.0, 0.3, 0.6, 0.9, 1.2])
        for sp in self.rooms["class_charms"].layout["spawns"]:
            self.assertEqual(surface_at(layout, *sp["pos"]).y, 1.2)
        ex = self.rooms["class_charms"].layout["interactables"][0]
        self.assertIsNotNone(surface_at(layout, *ex["pos"]))

    def test_thirty_seats_behind_desks_on_tiers(self):
        room = self.rooms["class_charms"]
        cap = load(os.path.join(REPO, "data", "classes", "schedule.json"))["classes"][0]["instance_cap"]
        self.assertEqual(len(room.seats), cap)
        layout = self.layouts["class_charms"]
        for seat in room.seats:
            s = surface_at(layout, *seat["pos"])
            self.assertIsNotNone(s, seat)
            self.assertGreater(s.y, 0.0)
            # The desk's near edge is DESK_FRONT ahead and still over this
            # level or the one below (the desk overhangs nothing walkable
            # higher up).
            x, y, z = seat["pos"]
            self.assertIsNotNone(surface_at(layout, x, y, z - sr.DESK_FRONT - 0.5, tol=0.31), seat)
        pos = [tuple(s["pos"]) for s in room.seats]
        for i, a in enumerate(pos):
            for b in pos[i + 1:]:
                self.assertGreater(math.dist(a, b), 1.0)

    def test_arena_stage_is_the_venue_ring(self):
        venue = load(os.path.join(REPO, "data", "dueling", "venues.json"))["venues"][0]
        layout = self.layouts["duel_arena"]
        stage = next(s for s in layout.surfaces if s.id == "arena_stage")
        self.assertEqual((stage.x0, stage.z0, stage.x1, stage.z1), (*venue["ring"]["min"], *venue["ring"]["max"]))
        for d in venue["duelists"]:
            self.assertEqual(surface_at(layout, *d["pos"]).id, "arena_stage")
        self.assertIsNotNone(surface_at(layout, *venue["spectators"]["pos"]))

    def test_budgets_materials_and_lights(self):
        for lid, room in self.rooms.items():
            self.assertLess(room.mesh.triangle_count(), 20000, lid)
            used = set(room.mesh.materials())
            self.assertTrue(used <= set(room.materials), (lid, used - set(room.materials)))
            self.assertLessEqual(len(used), 20, lid)  # one draw call per material
            self.assertTrue(1 <= len(room.lights) <= 6, lid)  # the client uses up to six
            lo, hi = room.mesh.bounds()
            for lt in room.lights:
                self.assertTrue(all(lo[k] - 0.01 <= lt["pos"][k] <= hi[k] + 0.01 for k in range(3)), (lid, lt["id"]))
                self.assertTrue(L.HEX_RE.match(lt["color"]))
            for f in room.mesh.faces:
                if f.material == "swatch":
                    self.assertTrue(all(0.0 < u < 1.0 and 0.0 < v < 1.0 for u, v in f.uvs), lid)

    def test_floors_drawn_at_walkable_heights(self):
        for lid, room in self.rooms.items():
            ups = []
            for f in room.mesh.faces:
                pts = room.mesh.face_points(f)
                n = mk.newell(pts)
                if mk.length(n) > 0 and n[1] / mk.length(n) > 0.99:
                    ups.append(pts)
            for w in room.walks:
                cx, cz = (w.x0 + w.x1) / 2, (w.z0 + w.z1) / 2
                hit = [p for p in ups if min(q[0] for q in p) <= cx <= max(q[0] for q in p)
                       and min(q[2] for q in p) <= cz <= max(q[2] for q in p) and abs(p[0][1] - w.y) < 0.02]
                self.assertTrue(hit, (lid, w.id))

    def test_risers_close_every_step(self):
        room = self.rooms["class_charms"]
        verticals = [room.mesh.face_points(f) for f in room.mesh.faces if f.material == "desk_dark"]
        for a in room.walks:
            for b in room.walks:
                if b.y <= a.y or abs(b.y - a.y - sr.TIER_RISE) > 1e-6:
                    continue
                # b sits one step above a along a shared z edge: a riser there.
                if abs(b.z0 - a.z1) < 1e-6 and min(a.x1, b.x1) - max(a.x0, b.x0) > 0.1:
                    x = (max(a.x0, b.x0) + min(a.x1, b.x1)) / 2
                    found = any(abs(p[0][2] - b.z0) < 1e-6 and min(q[0] for q in p) <= x <= max(q[0] for q in p)
                                and min(q[1] for q in p) <= a.y + 0.01 and max(q[1] for q in p) >= b.y - 0.01
                                for p in verticals)
                    self.assertTrue(found, (a.id, b.id))


class CastleDressingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = L.load_layout(LAYOUT)
        cls.kit = {p.id: p for p in kit.load_kit(PROPS)}

    def test_school_models_registered_with_budgets(self):
        for name in ("duel_stage", "duel_area_inlay", "spectator_bench", "duel_banner", "floating_candles",
                     "classroom_door", "notice_board"):
            self.assertIn(name, km.MODELS)
            k = km.build(name)
            self.assertLessEqual(k.mesh.triangle_count(), km.budget(name), name)
            for mat in k.mesh.materials():
                self.assertIn(mat, kit.TILES, (name, mat))

    def test_wall_pieces_stay_in_front_of_their_wall(self):
        for name in ("classroom_door", "notice_board", "duel_banner"):
            lo, _ = km.build(name).mesh.bounds()
            self.assertGreaterEqual(lo[2], -1e-6, name)

    def test_stage_dressing_fits_the_layout_stage(self):
        stage = self.layout.by_id("gh_duel_stage")
        prop = self.kit["dc_stage"]
        self.assertAlmostEqual(prop.pos[0], (stage.x0 + stage.x1) / 2)
        self.assertAlmostEqual(prop.pos[2], (stage.z0 + stage.z1) / 2)
        lo, hi = km.build("duel_stage").mesh.bounds()
        west = self.layout.by_id("gh_duel_steps_west")
        self.assertAlmostEqual(prop.pos[0] + lo[0], west.x0, delta=0.05)
        self.assertLessEqual(hi[2] - lo[2], (stage.z1 - stage.z0) + 0.1)
        self.assertAlmostEqual(stage.y, 0.9)

    def test_charms_door_at_its_interactable(self):
        door = next(i for i in self.layout.interactables if i.id == "charms_classroom_door")
        prop = self.kit["charms_door"]
        self.assertEqual(prop.cell, "tf_corridor_west")
        self.assertLess(math.dist((prop.pos[0], prop.pos[2]), (door.pos[0], door.pos[2])), door.radius)
        self.assertAlmostEqual(prop.pos[1], door.pos[1])
        _, hi = km.build("classroom_door").mesh.bounds()
        corridor = self.layout.by_id("tf_corridor_west")
        self.assertLess(prop.pos[1] + hi[1], corridor.y + corridor.ceiling)

    def test_dueling_area_dressed(self):
        area = next(a for a in self.layout.areas if a["id"] == "dueling_club") if hasattr(self.layout, "areas") else None
        raw = load(LAYOUT)
        area = area or next(a for a in raw["areas"] if a["id"] == "dueling_club")
        inlay = self.kit["dc_area"]
        args = dict(inlay.args)
        self.assertLessEqual(inlay.pos[0] - args["width"] / 2, area["max"][0])
        self.assertGreaterEqual(inlay.pos[0] - args["width"] / 2, area["min"][0] - 0.01)
        self.assertAlmostEqual(inlay.pos[2] - args["depth"] / 2, area["min"][2], delta=0.01)
        self.assertAlmostEqual(inlay.pos[2] + args["depth"] / 2, area["max"][2], delta=0.01)
        for pid in ("dc_bench_nw", "dc_bench_ne", "dc_bench_sw", "dc_bench_se"):
            x, _, z = self.kit[pid].pos
            self.assertTrue(area["min"][0] < x < area["max"][0] and area["min"][2] < z < area["max"][2], pid)

    def test_stage_drawn_without_railings(self):
        props = L.load_props(PROPS)
        open_edges = L.load_open_edges(PROPS)
        self.assertEqual(open_edges, {"gh_duel_stage", "gh_duel_steps_west", "gh_duel_steps_east"})

        # The stage's footprint, from the layout (with a 0.1 m margin).
        stage = self.layout.by_id("gh_duel_stage")
        x0, x1, z0, z1 = stage.x0 - 0.1, stage.x1 + 0.1, stage.z0 - 0.1, stage.z1 + 0.1

        def stage_railings(plan):
            return [p for p in plan.polys.get(("great_hall", "railing"), [])
                    if all(x0 <= q[0] <= x1 and z0 <= q[2] <= z1 for q in p)]

        self.assertTrue(stage_railings(L.plan_layout(self.layout, props)))
        self.assertFalse(stage_railings(L.plan_layout(self.layout, props, open_edges)))


if __name__ == "__main__":
    unittest.main()
