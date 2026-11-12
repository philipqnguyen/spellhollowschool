"""Unit tests for the art (docs/architecture.md): the
instance-room kit adapter (world/instance_kit.py), the Potions and the
shared classroom built from castle-kit pieces (world/class_rooms.py), the
class door plaques in the castle (castle_kit.json), the desk dressing piece
and the Transfiguration class icons (icons/icon_models.py). Pure Python;
run through tests/run_tests.py."""

import json
import math
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.path.dirname(HERE)
REPO = os.path.abspath(os.path.join(BLENDER, "..", ".."))
sys.path[:0] = [os.path.join(BLENDER, "world"), os.path.join(BLENDER, "icons"), BLENDER]

import class_rooms  # noqa: E402
import icon_models  # noqa: E402
import instance_kit as IK  # noqa: E402
import layout_lib as L  # noqa: E402
import palette  # noqa: E402
import school_layouts  # noqa: E402
from castle_kit import assemble as A  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import pieces as P  # noqa: E402
from castle_kit import schema as S  # noqa: E402
from castle_kit.pieces import geom  # noqa: E402
from castle_kit.pieces import school as SP  # noqa: E402
from castle_kit.pieces import school_2c as S2C  # noqa: E402

INSTANCES = os.path.join(REPO, "data", "world", "instances")
CLASSES = os.path.join(REPO, "data", "classes")
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class KitRoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rooms = class_rooms.build_all()
        cls.built = {lid: IK.build(room) for lid, room in cls.rooms.items()}
        cls.reports = {lid: IK.lint(room, cls.built[lid]) for lid, room in cls.rooms.items()}
        cls.schedule = load(os.path.join(CLASSES, "schedule.json"))

    def test_scheduled_rooms_are_built(self):
        layouts = {c["layout"] for c in self.schedule["classes"]}
        self.assertTrue({"class_potions", "class_classroom"} <= layouts)
        # The rooms' modules add class_defence,
        # class_divination (Rooms A) and class_astronomy (Rooms B).
        phase_2c = {"class_defence", "class_divination", "class_astronomy"}
        self.assertTrue(phase_2c <= layouts)
        # The Greenhouse and Paddock modules add
        # class_greenhouse and class_paddock.
        phase_2d = {"class_greenhouse", "class_paddock"}
        self.assertTrue(phase_2d <= layouts)
        # The Pitch module adds class_pitch (the Flying class).
        phase_3b = {"class_pitch"}
        self.assertTrue(phase_3b <= layouts)
        self.assertTrue({"class_potions", "class_classroom"} <= set(self.rooms))
        self.assertTrue(set(self.rooms) <= {"class_potions", "class_classroom"} | phase_2c | phase_2d | phase_3b)
        # The props a room animates belong to a built room.
        self.assertTrue(set(class_rooms.CLASS_PROPS) <= set(self.rooms))
        for lid in self.rooms:
            self.assertEqual(G.m5b_rules(lid), G.INSTANCE_RULES[lid])
            self.assertNotIn(lid, G.M5B_CELLS)

    def test_layout_files_generated_and_valid(self):
        texts = school_layouts.generate()
        for lid, room in self.rooms.items():
            with open(os.path.join(INSTANCES, f"{lid}.json"), encoding="utf-8") as f:
                self.assertEqual(f.read(), texts[lid], f"{lid}.json is out of date: run school_layouts.py --write")
            rep = L.validate(L.parse_layout(room.layout))
            self.assertEqual(rep.errors, [], lid)

    def test_contract_ids_and_professor_spot(self):
        for lid, room in self.rooms.items():
            lay = room.layout
            self.assertEqual([s["id"] for s in lay["surfaces"]], ["class_floor"])
            self.assertEqual([s["id"] for s in lay["spawns"]], ["class_entry_a", "class_entry_b"])
            self.assertEqual([(i["id"], i["kind"]) for i in lay["interactables"]], [("class_exit", "instance_exit")])
            prof = next(m for m in lay["landmarks"] if m["id"] == "class_professor")
            # Madam Swift stands before the students on the 60 x 40 m field
            # (world/pitch_course.py's contract, tests/test_pitch.py).
            self.assertEqual(prof["pos"], [0.0, 0.0, 10.0] if lid == "class_pitch" else [0.0, 0.0, -6.0])
        for cls in ("potions", "transfiguration"):
            c = load(os.path.join(CLASSES, f"{cls}.json"))
            self.assertEqual(c["professor"]["pos"], [0, 0, -6])

    def test_spawns_out_of_the_exits_reach(self):
        for lid, room in self.rooms.items():
            ex = room.layout["interactables"][0]
            for s in room.layout["spawns"]:
                self.assertGreater(math.dist((s["pos"][0], s["pos"][2]), (ex["pos"][0], ex["pos"][2])), ex["radius"], lid)

    def test_lint_clean_within_the_classroom_budget(self):
        for lid, rep in self.reports.items():
            self.assertEqual(rep.errors, [], lid)
            cb, _ = self.built[lid]
            self.assertEqual(cb.plan.budget, "classroom")
            self.assertLessEqual(cb.draws(), G.BUDGETS["classroom"].draws, lid)
            self.assertLessEqual(cb.triangles(), G.BUDGETS["classroom"].tris[1], lid)
            self.assertTrue(all(m.material.startswith("castle_") for m in cb.meshes.values()), lid)
            self.assertEqual(cb.lod_meshes, {}, lid)  # the instance loader has no LOD switch
            self.assertEqual(cb.extras, {}, lid)  # nor moving nodes
        self.assertGreaterEqual(self.built["class_potions"][0].triangles(), G.BUDGETS["classroom"].tris[0])

    def test_professor_spawns_and_exit_kept_clear(self):
        """Nothing of the kit but the floor (rugs, borders) stands within
        0.6 m of the professor's spot, the spawns and the exit, up to 2.2 m."""
        for lid, room in self.rooms.items():
            cb, _ = self.built[lid]
            # An animated prop standing on furniture (the greenhouse's plants on their
            # staging bench, y > 0) stands on what is under it; its own checks are the room's tests.
            props = class_rooms.CLASS_PROPS.get(lid, {})
            spots = [m["pos"] for m in room.layout["landmarks"] if m["id"] not in props or m["pos"][1] <= 0.0]
            spots += [s["pos"] for s in room.layout["spawns"]]
            spots.append(room.layout["interactables"][0]["pos"])
            for f in cb.faces:
                if f.src in ("floor", "base") or f.src.startswith("floor_border") or "rug" in f.src:
                    continue
                if max(p[1] for p in f.pts) < 0.031 or min(p[1] for p in f.pts) > 2.2:
                    continue
                if f.src.startswith("opening:") or ":blind_door" in f.src or f.src.startswith("class_floor:s0"):
                    continue  # the class door in the south wall, behind the exit
                for x, _, z in spots:
                    near = any(math.hypot(p[0] - x, p[2] - z) < 0.6 for p in f.pts)
                    self.assertFalse(near, f"{lid}: {f.src} stands at the spot {x, z}")

    def test_class_door_behind_the_exit(self):
        for lid, room in self.rooms.items():
            cb, _ = self.built[lid]
            doors = [o for o in cb.plan.openings if o.kind == "blind_door"]
            self.assertEqual(len(doors), 1, lid)
            d = doors[0]
            ex = room.layout["interactables"][0]["pos"]
            self.assertEqual(d.piece, G.CLASS_DOOR)
            self.assertAlmostEqual((d.t0 + d.t1) / 2, ex[0])
            # class_pitch's exit is the contract's, 1.5 m in from its gatehouse door.
            self.assertAlmostEqual(abs(d.c - ex[2]), 1.5 if lid == "class_pitch" else 0.5, places=3)

    def test_classroom_seats_a_full_section(self):
        cap = max(c["instance_cap"] for c in self.schedule["classes"] if c["layout"] == "class_classroom")
        seats = IK.seats(self.built["class_classroom"][0])
        self.assertGreaterEqual(len(seats), cap)
        lay = L.parse_layout(self.rooms["class_classroom"].layout)
        for s in seats:
            x, y, z = s["pos"]
            self.assertTrue(any(w.x0 <= x <= w.x1 and w.z0 <= z <= w.z1 and abs(w.y - y) < 1e-6 for w in lay.surfaces), s)
            self.assertAlmostEqual(abs(s["yaw"]), math.pi, places=4)  # facing the professor (north)
        self.assertGreater(self.reports["class_classroom"].stats.get("knee_boxes", 0), 0)

    def test_potions_cauldron_desks(self):
        room = self.rooms["class_potions"]
        cauldrons = [k for k in room.kit if k["model"] == "classroom_cauldron"]
        self.assertTrue(6 <= len(cauldrons) <= 8)
        self.assertEqual(room.spec["materials"]["castle_wall"], "castle_wall_d")

    def test_build_is_deterministic(self):
        for lid, room in self.rooms.items():
            again, _ = IK.build(room)
            self.assertEqual(again.digest(), self.built[lid][0].digest(), lid)

    def test_colours_from_palette_data(self):
        glow, castle = set(palette.GLOW.values()), set(palette.CASTLE.values())
        for lid, room in self.rooms.items():
            self.assertTrue(1 <= len(room.lights) <= 6, lid)
            for lt in room.lights:
                self.assertIn(lt["color"], glow, (lid, lt["id"]))
            self.assertIn(room.ambient["sky"], glow | castle)
            self.assertIn(room.ambient["ground"], glow | castle)
        for path in (os.path.join(BLENDER, "world", "class_rooms.py"), os.path.join(BLENDER, "world", "instance_kit.py"),
                     os.path.join(BLENDER, "castle_kit", "pieces", "school.py")):
            with open(path, encoding="utf-8") as f:
                self.assertIsNone(HEX.search(f.read()), path)

    def test_seat_rows_become_room_seats(self):
        cb = self.built["class_classroom"][0]
        self.assertEqual(len(IK.seats(cb)), len(cb.seats))
        for row, seat in zip(cb.seats, IK.seats(cb)):
            self.assertAlmostEqual(seat["pos"][1] + IK.ROOM_SEAT_HEIGHT, row[4], places=3)
            self.assertEqual((seat["front"], seat["back"], seat["drop"]), tuple(row[6:9]))


class ClassDoorPlaqueTests(unittest.TestCase):
    def test_plaque_matches_the_class_door(self):
        door = P.build(G.CLASS_DOOR)
        plaque = P.build("kit_class_plaque", y=door.meta["plaque"][2])
        self.assertEqual(plaque.meta["plaque"], door.meta["plaque"])
        tris = sum(len(f.pts) - 2 for f in plaque.faces)
        self.assertLessEqual(tris, 100)
        self.assertGreaterEqual(min(p[1] for f in plaque.faces for p in f.pts), 1.8)
        with self.assertRaises(ValueError):
            P.build("kit_class_plaque", y=1.0)

    def test_plaques_at_the_class_doors(self):
        """A plaque at every class door of the castle (Transfiguration and
        Potions; 2c: History of Magic, Defence, Divination, Astronomy), on
        its floor, by the door, over the blind door it marks."""
        kit = load(S.KIT_PATH)["cells"]
        layout = load(S.LAYOUT_PATH)
        doors = {i["id"]: i for i in layout["interactables"]}
        cells = ("ff_corridor_north", "dungeon_west", "l7_corridor_north")
        heroes = {h["id"]: h for c in cells for h in kit[c]["hero"] if h["piece"] == "kit_class_plaque"}
        pairs = (("transfiguration_plaque", "transfiguration_classroom_door"), ("potions_plaque", "potions_classroom_door"),
                 ("history_plaque", "history_classroom_door"), ("defence_plaque", "defence_classroom_door"),
                 ("divination_plaque", "divination_classroom_door"), ("astronomy_plaque", "astronomy_tower_door"))
        self.assertEqual(set(heroes), {h for h, _ in pairs})
        class_doors = {i["id"] for i in layout["interactables"] if i["kind"] == "class_door"}
        # Charms: its door piece; the two grounds gates are gate
        # pieces on the grounds (props.json kit entries).
        # The Pitch Gate is the pitch export's, in the `pitch` cell.
        grounds_gates = {"greenhouse_gate", "paddock_gate", "pitch_gate"}
        self.assertEqual({d for _, d in pairs} | {"charms_classroom_door"} | grounds_gates, class_doors)
        for hid, did in pairs:
            h, d = heroes[hid], doors[did]
            self.assertAlmostEqual(h["pos"][1], d["pos"][1])
            self.assertLess(math.dist((h["pos"][0], h["pos"][2]), (d["pos"][0], d["pos"][2])), 3.2, hid)
        # The seventh-floor plaques stand over the end walls' blind doors (at t -23).
        l7 = kit["l7_corridor_north"]["openings"]
        for hid, run in (("divination_plaque", "l7_corridor_north:w0"), ("astronomy_plaque", "l7_corridor_north:e0")):
            (door,) = [o for o in l7 if o["kind"] == "blind_door" and o["run"] == run]
            self.assertAlmostEqual(heroes[hid]["pos"][2], door["t"])
        # The Potions doorway stays open: nothing is placed across it.
        self.assertNotIn("dungeon_classroom_door", json.dumps(kit["dungeon_west"]["replaces"]))


class DeskObjectsTests(unittest.TestCase):
    def test_within_cap_and_on_the_table(self):
        for seed in range(1, 17):
            p = P.build("kit_desk_objects", seed=seed)
            pts = [q for f in p.faces for q in f.pts]
            self.assertLessEqual(sum(len(f.pts) - 2 for f in p.faces), SP.DESK_OBJECTS_TRIS)
            self.assertGreaterEqual(min(q[1] for q in pts), -1e-6)
            self.assertLessEqual(max(q[1] for q in pts), 0.27)
            self.assertLessEqual(max(abs(q[0]) for q in pts), 0.78)
            self.assertLessEqual(max(abs(q[2]) for q in pts), 0.43)
            self.assertTrue(p.walkthrough)

    def test_palette_names_have_slots(self):
        slots = {tuple(s) for s in load(S.TEXTURES_PATH)["props_slots"] if isinstance(s, list)}
        for zone, name in SP.ZONES.items():
            self.assertIn((SP.TILE[zone], "@" + name), slots, name)


class ClassIconTests(unittest.TestCase):
    def test_every_class_icon_key_has_a_model(self):
        picks = load(os.path.join(CLASSES, "picks.json"))
        keys = {i["icon"] for s in picks["sets"] for i in s["items"] if i.get("icon", "").startswith("icons/class/")}
        self.assertEqual({f"icons/class/{n}" for n in icon_models.CLASS_ICONS}, keys)
        # Transfiguration's 12, Divination's ten symbols, Herbology's 4 plants and 8
        # tools, Creature Keeping's 7 needs and 8 kinds of care.
        self.assertEqual(len(keys), 49)

    def test_models_and_palette_colours(self):
        ids = {c["id"] for c in load(os.path.join(REPO, "data", "items", "palette.json"))["colours"]}
        for name, (_, dyes) in icon_models.CLASS_ICONS.items():
            m = icon_models.build_class_icon(name)
            lo, hi = m.bounds()
            self.assertAlmostEqual(lo[1], 0.0, places=6)
            self.assertLess(max(hi[k] - lo[k] for k in range(3)), 0.5, name)
            self.assertLess(m.triangle_count(), 1500, name)
            self.assertEqual(len(dyes), 3)
            self.assertTrue(set(dyes) <= ids, name)


class Phase2cRoomsATests(unittest.TestCase):
    """The Defence drill
    dressing of the classroom shell, the Divination tower room, their pieces
    (castle_kit/pieces/school_2c.py) and the common rooms' floor seats with
    the furniture on them cut out (castle_kit/assemble.py)."""

    @classmethod
    def setUpClass(cls):
        cls.rooms = class_rooms.build_all()
        cls.built = {lid: IK.build(cls.rooms[lid]) for lid in ("class_defence", "class_divination", "class_classroom")}
        cls.schedule = load(os.path.join(CLASSES, "schedule.json"))

    def heroes(self, lid, piece):
        return [h for h in self.rooms[lid].spec["hero"] if h["piece"] == piece]

    def test_defence_is_the_classroom_shell_with_a_drill_dressing(self):
        shell, room = self.rooms["class_classroom"], self.rooms["class_defence"]
        for k in ("budget", "materials", "bay_target", "springing", "runs", "floor_border", "openings", "lanterns", "shade"):
            self.assertEqual(room.spec[k], shell.spec[k], k)
        vault = lambda r: [h for h in r.spec["hero"] if h["id"] == "vault"]  # noqa: E731
        self.assertEqual(vault(room), vault(shell))
        self.assertEqual(room.layout["surfaces"][0]["ceiling"], shell.layout["surfaces"][0]["ceiling"])
        self.assertFalse(self.heroes("class_defence", "kit_chair_study"))  # the floor is cleared
        self.assertEqual(len(self.heroes("class_defence", "kit_practice_target")), 4)
        self.assertEqual(len(self.heroes("class_defence", "kit_rug_4x3")), 6)  # the mats
        (ring,) = self.heroes("class_defence", "kit_drill_ring")
        self.assertLess(math.dist((ring["pos"][0], ring["pos"][2]), (0.0, -6.0)), 0.5)  # round the professor's spot
        # Transfiguration's dressing is unchanged: its desks and chairs are still there.
        self.assertEqual(len([h for h in shell.spec["hero"] if h["piece"] == "kit_chair_study"]), 32)

    def test_divination_seats_a_full_section_round_tables(self):
        cap = max(c["instance_cap"] for c in self.schedule["classes"] if c["layout"] == "class_divination")
        cb = self.built["class_divination"][0]
        seats = IK.seats(cb)
        self.assertGreaterEqual(len(seats), cap)
        self.assertEqual(len(self.heroes("class_divination", "kit_armchair")), len(seats))
        tables = self.heroes("class_divination", "kit_table_round")
        sets = self.heroes("class_divination", "kit_divination_set")
        self.assertEqual(len(sets), len(tables))  # a set on every table, the professor's included
        kinds = {h["args"]["kind"] for h in sets}
        self.assertEqual(kinds, {0, 1})  # cups and crystal balls
        for s in sets:  # on a table's top, at its middle
            self.assertTrue(any(math.dist((s["pos"][0], s["pos"][2]), (t["pos"][0], t["pos"][2])) < 1e-6
                                and abs(s["pos"][1] - 0.74) < 1e-6 for t in tables), s["id"])
        # Every student's armchair faces a table (its centre) from CHAIR_R or CHAIR_R4.
        student = [t for t in tables if t["id"] != "professor_table"]
        for a in self.heroes("class_divination", "kit_armchair"):
            x, _, z = a["pos"]
            fx, fz = math.sin(a["yaw"]), math.cos(a["yaw"])
            self.assertTrue(any(abs(math.dist((x, z), (t["pos"][0], t["pos"][2])) - r) < 1e-3
                                and abs(x + fx * r - t["pos"][0]) < 1e-3 and abs(z + fz * r - t["pos"][2]) < 1e-3
                                for t in student for r in (1.4, 1.5)), a["id"])
        self.assertEqual(self.rooms["class_divination"].spec["materials"]["castle_wall"], "castle_wall_b")
        self.assertEqual(len(self.heroes("class_divination", "kit_chandelier_ring")), 1)

    def test_divination_chandelier_hangs_still(self):
        # An instance room has no moving nodes: the ring chandelier merges into the props mesh.
        cb = self.built["class_divination"][0]
        self.assertEqual(cb.extras, {})
        self.assertFalse([n for n in cb.meshes if "candles_" in n])
        self.assertEqual(cb.plan.hero_groups, {})

    def test_divination_drapes_are_no_house(self):
        names = set()
        for u in self.built["class_divination"][0].uses:
            if u.role == "hero":
                piece = self.built["class_divination"][0].pieces[u.id]
                names |= {f.tag.rsplit("@", 1)[1] for f in piece.faces if "@" in f.tag}
        self.assertIn("tapestry_wine", names)
        self.assertFalse({"house_a", "house_b", "house_c", "house_d"} & names)
        slots = {tuple(s) for s in load(S.TEXTURES_PATH)["props_slots"] if isinstance(s, list)}
        self.assertIn(("fabric", "@tapestry_wine"), slots)
        with self.assertRaises(ValueError):
            P.build("kit_drapes_1.6x5.9", house=0, cloth=2)

    def test_pieces_within_caps_and_palette_slots(self):
        slots = {tuple(s) for s in load(S.TEXTURES_PATH)["props_slots"] if isinstance(s, list)}
        for zone, name in S2C.ZONES.items():
            self.assertIn((S2C.TILE[zone], "@" + name), slots, name)
        t = P.build("kit_practice_target")
        self.assertLessEqual(t.triangles(), S2C.PRACTICE_TARGET_TRIS)
        pts = [q for f in t.faces for q in f.pts]
        self.assertLessEqual(max(math.hypot(q[0], q[2]) for q in pts), 0.65)
        self.assertTrue(t.walkthrough)
        for r in (0.8, 1.5, 3.0):
            ring = P.build("kit_drill_ring", radius=r)
            self.assertLessEqual(ring.triangles(), S2C.DRILL_RING_TRIS)
            self.assertLessEqual(max(q[1] for f in ring.faces for q in f.pts), S2C.DRILL_RING_H + 1e-9)  # flat
        for kind in (0, 1):
            for n in range(S2C.SET_PLACES_MAX + 1):
                for c in (0, 1, 2, 3):
                    s = P.build("kit_divination_set", kind=kind, places=n, centre=c, spread=58.0)
                    pts = [q for f in s.faces for q in f.pts]
                    self.assertLessEqual(s.triangles(), S2C.DIVINATION_SET_TRIS)
                    if pts:
                        self.assertLessEqual(max(math.hypot(q[0], q[2]) for q in pts), 0.6)  # on the 1.4 m top
                        self.assertLessEqual(max(q[1] for q in pts), 0.4)
                        self.assertGreaterEqual(min(q[1] for q in pts), -1e-6)

    def test_new_room_modules_have_no_colour_literals(self):
        for name in ("school_rooms_defence.py", "school_rooms_divination.py"):
            with open(os.path.join(BLENDER, "world", name), encoding="utf-8") as f:
                self.assertIsNone(HEX.search(f.read()), name)


class FloorSeatCutTests(unittest.TestCase):
    """A floor seat (a rug, a hearth step) less the
    furniture standing on it (the common rooms' round coffee tables): no
    floor-seat point inside furniture."""

    def test_cut_splits_into_four(self):
        rows = A.cut_floor_seat([0.0, 0.0, 4.0, 3.0], [[1.3, 0.8, 2.7, 2.2]])
        self.assertEqual(len(rows), 4)
        self.assertAlmostEqual(sum((r[2] - r[0]) * (r[3] - r[1]) for r in rows), 12.0 - 1.4 * 1.4)
        for r in rows:  # none overlaps the table
            self.assertTrue(r[2] <= 1.3 + 1e-9 or r[0] >= 2.7 - 1e-9 or r[3] <= 0.8 + 1e-9 or r[1] >= 2.2 - 1e-9, r)

    def test_cut_drops_slivers_and_keeps_clear_rugs(self):
        self.assertEqual(A.cut_floor_seat([0.0, 0.0, 2.0, 2.0], [[3.0, 3.0, 4.0, 4.0]]), [[0.0, 0.0, 2.0, 2.0]])
        self.assertEqual(A.cut_floor_seat([0.0, 0.0, 2.0, 2.0], [[-1.0, -1.0, 3.0, 3.0]]), [])
        for r in A.cut_floor_seat([0.0, 0.0, 2.0, 2.0], [[0.5, 0.5, 1.5, 1.5]]):
            self.assertGreaterEqual(min(r[2] - r[0], r[3] - r[1]), G.FLOOR_SEAT_MIN - 1e-9)

    def test_common_rooms_floor_seats_clear_of_their_tables(self):
        b = A.Builder.from_files()
        for cell in ("common_room_house_a", "common_room_house_c", "common_room_house_d"):
            cb = b.build(cell)
            tables = []
            for u in cb.uses:
                if u.role == "hero" and u.piece in ("kit_table_round", "kit_armchair", "kit_sofa_2.4", "kit_sofa_3.2",
                                                    "kit_chair_study"):
                    pl = geom.Placement(u.origin, u.yaw)  # what stands under a floor sitter's head
                    tables.append([q for f in cb.pieces[u.id].faces for q in (pl.point(p) for p in f.pts)
                                   if q[1] - u.origin[1] < A.FLOOR_SEAT_CLEAR])
            rugs = [u for u in cb.uses if u.role == "hero" and u.piece.startswith("kit_rug_")]
            self.assertTrue(rugs, cell)
            self.assertGreaterEqual(len(cb.floor_seats), len(rugs), cell)  # every rug keeps a floor seat
            e = 1e-3  # the rows are rounded to mm
            for x0, z0, x1, z1, _ in cb.floor_seats:
                for pts in tables:
                    tx0, tx1 = min(q[0] for q in pts), max(q[0] for q in pts)
                    tz0, tz1 = min(q[2] for q in pts), max(q[2] for q in pts)
                    self.assertFalse(tx0 < x1 - e and tx1 > x0 + e and tz0 < z1 - e and tz1 > z0 + e, (cell, [x0, z0, x1, z1]))
            round_tables = [u for u in cb.uses if u.role == "hero" and u.piece == "kit_table_round"]
            self.assertTrue(round_tables, cell)


if __name__ == "__main__":
    unittest.main()
