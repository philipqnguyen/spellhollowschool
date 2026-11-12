"""Unit tests for the Astronomy class room (Rooms B, docs/
architecture.md): `class_astronomy`, a tower-top platform open
to the night sky (world/school_rooms_astronomy.py), its pieces
(castle_kit/pieces/astronomy.py: the stair head, the telescope) and the
instance-room adapter's head door (world/instance_kit.py KitRoom.head_door).
The generic class-room checks (lint, budget, contract ids, the door behind
the exit, determinism, palette colours) are test_class_rooms.py's, which
covers this room too. Pure Python; run through tests/run_tests.py."""

import json
import math
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.path.dirname(HERE)
REPO = os.path.abspath(os.path.join(BLENDER, "..", ".."))
sys.path[:0] = [os.path.join(BLENDER, "world"), BLENDER]

import class_rooms  # noqa: E402
import instance_kit as IK  # noqa: E402
import layout_lib as L  # noqa: E402
import palette  # noqa: E402
import school_rooms_astronomy as SA  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import pieces as P  # noqa: E402
from castle_kit import schema as S  # noqa: E402
from castle_kit.pieces import astronomy as AP  # noqa: E402
from castle_kit.pieces import facades  # noqa: E402

LID = "class_astronomy"
EDGE = 0.2  # a coping may oversail the floor's edge this far (the stair head's, 0.18 m, at 6.4 m)
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class AstronomyRoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.room = class_rooms.build_all()[LID]
        cls.cb, cls.b = IK.build(cls.room)
        cls.rep = IK.lint(cls.room, (cls.cb, cls.b))
        cls.hero = {h["id"]: h for h in cls.room.spec["hero"]}
        sched = load(os.path.join(REPO, "data", "classes", "schedule.json"))
        cls.cap = max(c["instance_cap"] for c in sched["classes"] if c["layout"] == LID)

    def test_merged_into_the_class_rooms(self):
        self.assertIs(class_rooms.KIT_ROOMS[LID], SA.class_astronomy)
        self.assertEqual(G.m5b_rules(LID), "upper_rooms")
        self.assertEqual(self.rep.errors, [])

    def test_open_to_the_sky(self):
        """One grounds floor with no ceiling (nothing roofs it: the client's
        night sky shows), and no stone, trim or ceiling face over the floor
        above head height but the class door's surround on its edge and the
        stair head's parapet copings oversailing it (EDGE)."""
        lay = L.parse_layout(self.room.layout)
        (s,) = lay.surfaces
        self.assertEqual((s.id, s.kind, s.ceiling), ("class_floor", "grounds", None))
        self.assertFalse(s.roofed)
        self.assertNotIn("castle_ceiling", {m.material for m in self.cb.meshes.values()})
        for f in self.cb.faces:
            if not f.material.startswith(("castle_wall", "castle_trim", "castle_ceiling")):
                continue
            if f.src.startswith("blind_door:"):
                continue
            over = [p for p in f.pts if s.x0 + EDGE < p[0] < s.x1 - EDGE and s.z0 + EDGE < p[2] < s.z1 - EDGE]
            self.assertFalse(any(p[1] > 2.6 for p in over), f"{f.src} roofs the platform")

    def test_safe_edges_all_round(self):
        """The floor ends at the low wall's inner faces; along every edge the
        parapet (sills at 1.15 m, merlons to 2.5 m) or the stair head stands,
        with no gap a player could reach; nothing of the parapet enters the floor."""
        pp = G.PLATFORM_PARAPET
        self.assertGreaterEqual(SA.LOW_WALL + pp["breast"], 1.1)
        self.assertGreaterEqual(SA.LOW_WALL + pp["height"], 2.4)
        spans = {"n": [], "s": [], "e": [], "w": []}
        for hid, h in self.hero.items():
            if h["piece"] != G.PARAPET_CRENEL:
                continue
            x, _, z = h["pos"]
            yaw, length = h["yaw"], h["args"]["length"]
            ux, uz = math.cos(yaw), -math.sin(yaw)  # the run's direction (geom.rot_y of local +x)
            nx, nz = math.sin(yaw), math.cos(yaw)  # its outward normal
            side = ("e" if nx > 0.5 else "w") if abs(nx) > 0.5 else ("s" if nz > 0.5 else "n")
            ends = sorted((x, x + ux * length) if side in "ns" else (z, z + uz * length))
            spans[side].append(tuple(ends))
            self.assertAlmostEqual(h["pos"][1], SA.LOW_WALL)
        half = SA.STAIR_HEAD["width"] / 2.0
        spans["s"].append((-half, half))  # the stair head's face
        for side, (lo, hi) in (("n", (SA.X0, SA.X1)), ("s", (SA.X0, SA.X1)), ("e", (SA.Z0, SA.Z1)), ("w", (SA.Z0, SA.Z1))):
            t = lo
            for a, b in sorted(spans[side]):
                self.assertLessEqual(a, t + 0.15, f"{side}: a gap at {t}")  # (the copings' clears: narrower than anyone)
                t = max(t, b)
            self.assertGreaterEqual(t, hi - 0.15, side)
        self.assertLessEqual(self.rep.stats["intrusion_max"], 0.15)  # the door's surround only
        # The low wall stands on every side but under the stair head.
        walls = [f for f in self.cb.faces if f.src == "base" and f.material.startswith("castle_wall")
                 and abs(f.normal()[1]) < 0.1]
        for x in (-9.0, -5.0, 5.0, 9.0):
            self.assertTrue(any(all(abs(p[2] - SA.Z1) < 1e-6 for p in f.pts) and min(p[0] for p in f.pts) <= x
                                <= max(p[0] for p in f.pts) for f in walls), x)
        for x in (-2.5, 0.0, 2.5):
            self.assertFalse(any(all(abs(p[2] - SA.Z1) < 1e-6 for p in f.pts) and min(p[0] for p in f.pts) < x
                                 < max(p[0] for p in f.pts) for f in walls), f"low wall in the stair head at {x}")

    def test_class_door_in_the_stair_head(self):
        (door,) = [o for o in self.cb.plan.openings if o.kind == "blind_door"]
        self.assertEqual(door.piece, G.CLASS_DOOR)
        self.assertEqual((door.t0, door.t1, door.c, door.sign), (-1.5, 1.5, SA.Z1, -1.0))
        head = self.hero["stair_head"]
        self.assertEqual(head["pos"], [0.0, 0.0, SA.Z1])
        self.assertAlmostEqual(head["yaw"], math.pi, places=6)
        meta = P.build(head["piece"], **head["args"]).meta
        self.assertEqual(meta["door"][:2], (-1.5, 1.5))
        self.assertAlmostEqual(meta["door"][2], G.OPENING_PIECES[G.CLASS_DOOR].height)
        plaque = P.build(G.CLASS_DOOR).meta["plaque"]
        self.assertLess(plaque[3], SA.STAIR_HEAD["height"])  # the plaque on the face over the door
        self.assertEqual(self.room.head_door["span"], [-3.0, 3.0])
        lanterns = [h for h in self.hero.values() if h["piece"] == "kit_lantern_wall"]
        self.assertEqual(len(lanterns), 2)
        for h in lanterns:
            self.assertLess(abs(h["pos"][0]), SA.STAIR_HEAD["width"] / 2.0)
            self.assertGreater(abs(h["pos"][0]) - 0.18, 1.5 + 0.3)  # clear of the door's surround

    def test_head_door_checks(self):
        room = SA.class_astronomy()
        room.head_door = dict(room.head_door, span=[-2.0, 2.0])  # no margin either side of the door
        with self.assertRaises(IK.KitRoomError):
            IK.build(room)
        room = SA.class_astronomy()
        room.head_door = dict(room.head_door, piece="kit_niche_2x3.5")
        with self.assertRaises(IK.KitRoomError):
            IK.build(room)

    def test_a_telescope_per_pair_and_a_seat_each(self):
        scopes = [h for hid, h in self.hero.items() if h["piece"] == G.TELESCOPE and hid != "telescope_professor"]
        seats = IK.seats(self.cb)
        self.assertGreaterEqual(len(seats), self.cap)
        self.assertGreaterEqual(len(scopes) * 2, self.cap)
        lay = L.parse_layout(self.room.layout)
        (s,) = lay.surfaces
        for st in seats:
            x, y, z = st["pos"]
            self.assertTrue(s.contains(x, z, 0.0) and abs(y - s.y) < 1e-6, st)
            self.assertAlmostEqual(abs(st["yaw"]), math.pi, places=4)  # facing the professor (north)
        for t in scopes:
            near = [st for st in seats if math.dist((st["pos"][0], st["pos"][2]), (t["pos"][0], t["pos"][2])) < 1.2]
            self.assertEqual(len(near), 2, t)
            self.assertAlmostEqual(t["yaw"], math.pi, places=6)  # aimed north, over the professor
        self.assertGreater(self.rep.stats["knee_boxes"], 0)
        # The entry strip stays clear: nothing of the dressing within 1.5 m of the exit.
        ex = self.room.layout["interactables"][0]["pos"]
        for h in self.hero.values():
            if h["piece"] in (G.TELESCOPE, "kit_chair_study"):
                self.assertGreater(math.dist((h["pos"][0], h["pos"][2]), (ex[0], ex[2])), 2.0, h["id"])

    def test_lights_and_ambient_from_the_palette(self):
        self.assertEqual(self.room.lights[0]["id"], "moon")
        self.assertEqual(self.room.lights[0]["color"], palette.GLOW["sky"])
        self.assertGreater(self.room.lights[0]["pos"][1], 10.0)
        for path in (SA.__file__, AP.__file__):
            with open(path, encoding="utf-8") as f:
                self.assertIsNone(HEX.search(f.read()), path)


class AstronomyPieceTests(unittest.TestCase):
    def test_telescope(self):
        slots = {tuple(s) for s in load(S.TEXTURES_PATH)["props_slots"] if isinstance(s, list)}
        for zone, name in AP.ZONES.items():
            self.assertIn((AP.TILE[zone], "@" + name), slots, name)
        for seed in range(1, 17):
            p = P.build(G.TELESCOPE, seed=seed)
            pts = [q for f in p.faces for q in f.pts]
            self.assertLessEqual(p.triangles(), G.TELESCOPE_TRIS)
            self.assertAlmostEqual(min(q[1] for q in pts), 0.0, places=6)
            self.assertLess(max(q[1] for q in pts), 2.0)
            self.assertLess(max(math.hypot(q[0], q[2]) for q in pts), 1.1)
            self.assertTrue(p.walkthrough)
            self.assertTrue(all(f.tag.startswith("castle_props|kit:") for f in p.faces))
            # The tube points the way it is aimed (+z) and up.
            far = max(pts, key=lambda q: q[2])
            self.assertGreater(far[1], AP.HEAD)
        a = P.build(G.TELESCOPE, seed=3)
        b = P.build(G.TELESCOPE, seed=3)
        self.assertEqual([f.pts for f in a.faces], [f.pts for f in b.faces])
        big = P.build(G.TELESCOPE, seed=3, scale=1.5)
        self.assertGreater(max(q[1] for f in big.faces for q in f.pts), max(q[1] for f in a.faces for q in f.pts))
        for bad in ({"scale": 2.0}, {"tilt": 1.4}):
            with self.assertRaises(ValueError):
                P.build(G.TELESCOPE, **bad)

    def test_stair_head(self):
        p = P.build(G.STAIR_HEAD, **SA.STAIR_HEAD)
        self.assertLessEqual(p.triangles(), G.STAIR_HEAD_TRIS)
        self.assertTrue(all(f.tag == AP.WALL for f in p.faces))
        w, d, h = SA.STAIR_HEAD["width"], SA.STAIR_HEAD["depth"], SA.STAIR_HEAD["height"]
        pts = [q for f in p.faces for q in f.pts]
        self.assertLessEqual(max(q[2] for q in pts), facades.parapet(4.0).meta["front"] + 0.06 + 1e-6)  # its parapet's copings
        self.assertGreaterEqual(min(q[2] for q in pts), -d - 0.2)
        self.assertAlmostEqual(max(q[1] for q in pts), h + 1.4)
        # Nothing of the face in the door's hole.
        x0, x1, top = p.meta["door"]
        for f in p.faces:
            if all(abs(q[2]) < 1e-6 for q in f.pts):
                cx = sum(q[0] for q in f.pts) / len(f.pts)
                cy = sum(q[1] for q in f.pts) / len(f.pts)
                self.assertFalse(x0 < cx < x1 and cy < top, f.pts)
        self.assertEqual(p.meta["size"], (w, d, h))
        with self.assertRaises(ValueError):
            P.build(G.STAIR_HEAD, height=5.0)
        with self.assertRaises(ValueError):
            P.build(G.STAIR_HEAD, width=5.0, door_w=4.5)

    def test_in_the_catalogue(self):
        for name in (G.STAIR_HEAD, G.TELESCOPE):
            self.assertIn(name, P.CATALOGUE)
            self.assertGreater(P.build(name, **P.CATALOGUE[name]).triangles(), 0)


class NightIconTests(unittest.TestCase):
    """The night-only items' icon looks (icons/icon_models.py, by asset key):
    the star-chart recipe cards, the Moonlit Glow Draught's crescent charm,
    the Comet Tail Tonic's comet (instead of the Pick-Me-Up Tonic steam), the
    Starlight Silver Dye's starred label."""

    IDS = (3111, 3311, 3312, 3427, 3428, 3429)

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, os.path.join(BLENDER, "icons"))
        import icon_models

        cls.M = icon_models
        cat = load(os.path.join(REPO, "data", "items", "catalog.json"))
        items = cat["items"] if isinstance(cat, dict) else cat
        cls.items = {it["id"]: it for it in items}

    def test_every_night_item_has_its_look(self):
        for iid in self.IDS:
            it = self.items[iid]
            self.assertIn("night", it["tags"])
            plain = dict(it, asset=it["asset"].rsplit("/", 1)[0] + "/plain")
            m, base = self.M.build(it), self.M.build(plain)
            self.assertAlmostEqual(m.bounds()[0][1], 0.0, places=6)
            self.assertLess(m.triangle_count(), 3000)
            self.assertNotEqual(m.triangle_count(), base.triangle_count(), iid)  # its own look, not the kind's default
            self.assertTrue(m.zones() <= {0, 1, 2, 3})

    def test_star_chart_cards_share_one_key(self):
        keys = {self.items[i]["asset"] for i in (3427, 3428, 3429)}
        self.assertEqual(keys, {"icons/pattern/star_chart"})
        self.assertIn("star_chart", self.M.SKETCHES)


if __name__ == "__main__":
    unittest.main()
