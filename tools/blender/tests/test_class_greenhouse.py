"""Unit tests for the Greenhouse art (docs/
architecture.md): the Herbology room `class_greenhouse`
(world/school_rooms_greenhouse.py), its pieces (castle_kit/pieces/
greenhouse.py), the four class plants (world/greenhouse_plants.py, the
room's CLASS_PROPS), Herbology's class icons (icons/
icon_models_herbology.py), and the two 2c fixes: the Astronomy platform
moonlit (world/school_rooms_astronomy.py) and the Divination armchairs in
cloth (world/school_rooms_divination.py). The generic class-room checks
(lint, budget, contract ids, the door behind the exit, determinism, palette
light colours) are test_class_rooms.py's, which covers this room too. Pure
Python; run through tests/run_tests.py."""

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
import greenhouse_plants as GP  # noqa: E402
import icon_models  # noqa: E402
import icon_models_herbology as IH  # noqa: E402
import instance_kit as IK  # noqa: E402
import kit  # noqa: E402
import palette  # noqa: E402
import school_rooms_astronomy as SA  # noqa: E402
import school_rooms_divination as SD  # noqa: E402
import school_rooms_greenhouse as SG  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import pieces as P  # noqa: E402
from castle_kit import props_atlas as PA  # noqa: E402
from castle_kit import schema as S  # noqa: E402
from castle_kit.pieces import greenhouse as GH  # noqa: E402

LID = "class_greenhouse"
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")
PLANTS = ("plant_vine", "plant_root", "plant_briar", "plant_fern")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class GreenhouseRoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.room = class_rooms.build_all()[LID]
        cls.cb, cls.b = IK.build(cls.room)
        cls.schedule = load(os.path.join(REPO, "data", "classes", "schedule.json"))

    def heroes(self, piece):
        return [h for h in self.room.spec["hero"] if h["piece"] == piece]

    def test_room_is_merged_with_its_props(self):
        self.assertIn(LID, class_rooms.KIT_ROOMS)
        self.assertEqual(set(class_rooms.CLASS_PROPS[LID]), set(PLANTS))
        self.assertEqual(G.INSTANCE_RULES[LID], "upper_rooms")
        self.assertIn(LID, {c["layout"] for c in self.schedule["classes"] if c["id"] == "herbology"})

    def test_lint_and_budget(self):
        rep = IK.lint(self.room, (self.cb, self.b))
        self.assertEqual(rep.errors, [])
        self.assertLessEqual(self.cb.draws(), G.BUDGETS["classroom"].draws)
        self.assertLessEqual(self.cb.triangles(), G.BUDGETS["classroom"].tris[1])

    def test_open_floor_under_the_glasshouse(self):
        (surface,) = self.room.layout["surfaces"]
        self.assertEqual(surface["kind"], "grounds")  # the low wall is the dwarf wall; nothing roofs the floor
        self.assertNotIn("ceiling", surface)
        (gh,) = self.heroes("kit_glasshouse")
        p = P.build("kit_glasshouse", **gh["args"])
        hx, hz = p.meta["glazing"]
        self.assertGreater(hx, -SG.X0)
        self.assertGreater(hz, -SG.Z0)
        self.assertAlmostEqual(gh["args"]["sill"], SG.LOW_WALL)  # the glazing stands on the low wall
        self.assertGreater(p.meta["tie"], 3.2)  # the trusses' ties well over the heads
        # Opaque panes, palette-tinted: the glass tile in glasshouse_glass, no glowing tile.
        tags = {f.tag for f in p.faces}
        self.assertIn(G.palette_tag("glass", "glasshouse_glass"), tags)
        self.assertFalse([t for t in tags if ":glow" in t])

    def test_plants_stand_on_their_pots_earth(self):
        lms = {m["id"]: m for m in self.room.layout["landmarks"]}
        (staging,) = self.heroes("kit_plant_staging")
        p = P.build("kit_plant_staging", **staging["args"])
        self.assertEqual(len(p.meta["pots"]), len(PLANTS))
        spots = sorted((staging["pos"][0] + x, GH.PLANT_SOIL_Y, staging["pos"][2]) for x in p.meta["pots"])
        self.assertEqual(sorted(tuple(lms[pid]["pos"]) for pid in PLANTS), spots)
        # The contract's spots: on a bench between the professor and the class.
        self.assertEqual([lms[pid]["pos"] for pid in PLANTS],
                         [[-3.0, 0.9, -4.0], [-1.0, 0.9, -4.0], [1.0, 0.9, -4.0], [3.0, 0.9, -4.0]])
        prof = lms["class_professor"]["pos"]
        self.assertLess(prof[2], staging["pos"][2])
        # The plants' pots' earth is the staging piece's soil (in the built cell too).
        soil = [q for f in self.cb.faces if f.src == "hero:plant_staging" and "@soil" in (f.arg or "") for q in f.pts]
        for spot in spots:  # the earth's middle (its lowest point) is the landmark
            self.assertTrue(any(math.dist(q, spot) < 1e-3 for q in soil), spot)

    def test_a_pot_for_every_student(self):
        cap = max(c["instance_cap"] for c in self.schedule["classes"] if c["layout"] == LID)
        benches = [h for h in self.heroes("kit_potting_bench") if h["id"].startswith("bench_")]
        places = sum(int(h["args"]["places"]) for h in benches)
        self.assertGreaterEqual(places, cap)
        for h in benches:  # facing the professor across the room, either side of the aisle
            self.assertEqual(h["yaw"], 0.0)
            self.assertGreater(h["pos"][2], -3.0)
            half = h["args"]["length"] / 2.0
            self.assertGreaterEqual(abs(h["pos"][0]) - half, 1.5)
        exit_ = self.room.layout["interactables"][0]["pos"]
        self.assertGreater(exit_[2], max(h["pos"][2] for h in benches) + 1.5)

    def test_pieces_within_caps_and_palette_slots(self):
        slots = {tuple(s) for s in load(S.TEXTURES_PATH)["props_slots"] if isinstance(s, list)}
        for zone, name in GH.ZONES.items():
            self.assertIn((GH.TILE[zone], "@" + name), slots, name)
        self.assertLessEqual(P.build("kit_glasshouse").triangles(), GH.GLASSHOUSE_TRIS)
        self.assertLessEqual(P.build("kit_glasshouse", width=30.0, depth=30.0).triangles(), GH.GLASSHOUSE_TRIS * 2)
        self.assertLessEqual(P.build("kit_glasshouse_porch").triangles(), GH.PORCH_TRIS)
        for length in (1.6, 4.0, 4.8):
            n = int(length / GH.PLACE_PITCH + 1e-9)
            b = P.build("kit_potting_bench", length=length, places=n, seed=3)
            self.assertLessEqual(b.triangles(), GH.POTTING_BENCH_TRIS)
            self.assertTrue(b.walkthrough)
            top = max(q[1] for f in b.faces for q in f.pts)
            self.assertLess(top, GH.POTTING_BENCH_TOP + 0.35)
        self.assertLessEqual(P.build("kit_plant_staging").triangles(), GH.PLANT_STAGING_TRIS)
        for bad in (lambda: P.build("kit_potting_bench", length=2.0, places=4),
                    lambda: P.build("kit_glasshouse", door=6.0, door_top=9.0),
                    lambda: P.build("kit_plant_staging", length=3.0, count=4)):
            with self.assertRaises(ValueError):
                bad()

    def test_build_is_deterministic(self):
        again, _ = IK.build(class_rooms.build_all()[LID])
        self.assertEqual(again.digest(), self.cb.digest())

    def test_no_colour_literals(self):
        for path in (os.path.join(BLENDER, "world", "school_rooms_greenhouse.py"),
                     os.path.join(BLENDER, "world", "greenhouse_plants.py"),
                     os.path.join(BLENDER, "castle_kit", "pieces", "greenhouse.py"),
                     os.path.join(BLENDER, "icons", "icon_models_herbology.py")):
            with open(path, encoding="utf-8") as f:
                self.assertIsNone(HEX.search(f.read()), path)


class GreenhousePlantTests(unittest.TestCase):
    def test_each_plant_within_the_prop_contract(self):
        names = PA.palette()
        for pid in PLANTS:
            m, zones = GP.build(pid)
            self.assertLessEqual(m.triangle_count(), GP.PLANT_TRIS, pid)
            lo, hi = m.bounds()
            self.assertLessEqual(lo[1], 1e-6, pid)  # the origin at the base: it stands on the earth
            self.assertGreaterEqual(lo[1], -0.1, pid)  # (a root may sink a little into it)
            self.assertTrue(0.4 <= hi[1] <= 1.4, pid)
            self.assertLess(max(abs(lo[0]), abs(hi[0]), abs(lo[2]), abs(hi[2])), 0.75, pid)
            for f in m.faces:
                self.assertIn(f.material, kit.TILES, pid)
                self.assertTrue(kit.TILES[f.material][2], (pid, f.material))  # a tinted tile: its palette colour
                self.assertFalse(kit.TILES[f.material][3], (pid, f.material))  # nothing glows
                self.assertIn(zones[f.zone], names, (pid, f.zone))
            again, _ = GP.build(pid)
            self.assertEqual(again.verts, m.verts, pid)  # deterministic

    def test_plants_face_the_students(self):
        # The Gnashvine's jaws and the Shriekroot's face are on their +z side.
        vine, _ = GP.build("plant_vine")
        teeth = [vine.verts[i] for f in vine.faces if f.zone == GP.TOOTH for i in f.idx]
        self.assertGreater(min(q[2] for q in teeth), 0.0)
        root, _ = GP.build("plant_root")
        eyes = [root.verts[i] for f in root.faces if f.zone == GP.DARK for i in f.idx]
        self.assertGreater(min(q[2] for q in eyes), 0.05)

    def test_prop_ids_match_the_layout_and_the_prompts(self):
        picks = load(os.path.join(REPO, "data", "classes", "picks.json"))
        (plants,) = [s for s in picks["sets"] if s["id"] == "herbology_plants"]
        prefixes = {i["key"].split("_", 1)[0] for i in plants["items"]}
        self.assertEqual({f"plant_{p}" for p in prefixes}, set(PLANTS))
        lms = {m["id"] for m in load(os.path.join(REPO, "data", "world", "instances", f"{LID}.json"))["landmarks"]}
        self.assertTrue(set(PLANTS) <= lms)


class HerbologyIconTests(unittest.TestCase):
    def test_twelve_icons_from_their_module(self):
        keys = {k for k in icon_models.CLASS_ICONS if k.startswith(("plant_", "tool_"))}
        self.assertEqual(keys, set(IH.HERBOLOGY_ICONS))
        self.assertEqual(len(keys), 12)
        ids = {c["id"] for c in load(os.path.join(REPO, "data", "items", "palette.json"))["colours"]}
        for name, (_, dyes) in IH.HERBOLOGY_ICONS.items():
            self.assertIs(icon_models.CLASS_ICONS[name], IH.HERBOLOGY_ICONS[name])
            m = icon_models.build_class_icon(name)
            self.assertEqual(m.zones(), {1, 2, 3}, name)
            self.assertTrue(set(dyes) <= ids, name)


class Phase2cFixTests(unittest.TestCase):
    """The 2c open items fixed in 2d: the Astronomy platform moonlit, the
    Divination armchairs upholstered in a lighter cloth."""

    def test_astronomy_is_moonlit(self):
        room = class_rooms.build_all()["class_astronomy"]
        self.assertFalse([h for h in room.spec["hero"] if h["piece"] == "kit_lamp_post"])
        moon = room.lights[0]
        self.assertEqual(moon["id"], "moon")
        self.assertEqual(moon["color"], palette.GLOW["sky"])
        self.assertLessEqual(moon["intensity"], 8.0)
        warm = [lt for lt in room.lights if lt["color"] != palette.GLOW["sky"]]
        for lt in warm:  # the only warm light is the stair door's, and it stays by the door
            self.assertLessEqual(lt["range"], 6.0)
            self.assertLessEqual(lt["intensity"], 3.0)
            self.assertGreater(lt["pos"][2], SA.Z1 - 2.0)
        self.assertLessEqual(room.ambient["intensity"], 0.3)
        self.assertEqual(room.ambient["sky"], palette.GLOW["sky"])

    def test_divination_armchairs_in_cloth(self):
        room = class_rooms.build_all()["class_divination"]
        chairs = [h for h in room.spec["hero"] if h["piece"] == "kit_armchair"]
        self.assertTrue(chairs)
        self.assertEqual({h["args"]["cover"] for h in chairs}, {SD.ARMCHAIR_COVER})
        self.assertEqual(G.FURNITURE_COVERS[SD.ARMCHAIR_COVER], "upholstery_rose")
        p = P.build("kit_armchair", cover=SD.ARMCHAIR_COVER)
        self.assertIn(G.palette_tag("fabric", "upholstery_rose"), {f.tag for f in p.faces})
        # Lighter than the leather it replaces.
        names = PA.palette()
        luma = lambda h: sum(w * int(h[i:i + 2], 16) for w, i in ((0.2126, 1), (0.7152, 3), (0.0722, 5)))  # noqa: E731
        self.assertGreater(luma(names["upholstery_rose"]), luma(names["upholstery_leather"]) * 1.5)
        # Every other room keeps its covers: the castle's armchairs and sofas use 0..4 only.
        self.assertEqual(G.FURNITURE_COVERS[:5], ("house_a", "house_b", "house_c", "house_d", "upholstery_leather"))
        kit_json = load(S.KIT_PATH)
        for cid, cell in kit_json["cells"].items():
            for h in cell.get("hero", []):
                if h["piece"] == "kit_armchair" or h["piece"].startswith("kit_sofa"):
                    self.assertLess(h.get("args", {}).get("cover", 0), 5, (cid, h["id"]))


if __name__ == "__main__":
    unittest.main()
