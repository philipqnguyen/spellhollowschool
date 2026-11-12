"""Unit tests for the Flying Pitch (docs/architecture.md):
the pitch pieces (castle_kit/pieces/pitch.py), the resident `pitch`
cell (world/pitch_build.py, exported by world/pitch_export.py through greybox.py's
outdoor hook), the Flying class's practice field `class_pitch`
(world/school_rooms_pitch.py) and its `ring_hoop` prop, and the Flying icons
(icons/icon_models_flying.py, the Featherlight bottle). The generic class-room
checks (lint, budget, contract ids, the door behind the exit, determinism,
palette lights) are test_class_rooms.py's, which covers class_pitch too. Pure
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
import icon_models  # noqa: E402
import instance_kit as IK  # noqa: E402
import layout_lib as L  # noqa: E402
import outdoor  # noqa: E402
import pitch_build as PB  # noqa: E402
import pitch_course as PC  # noqa: E402
import school_rooms_pitch as SRP  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import pieces as P  # noqa: E402
from castle_kit import schema as S  # noqa: E402
from castle_kit.pieces import hall_furniture as HF  # noqa: E402
from castle_kit.pieces import pitch as PP  # noqa: E402

HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")
LAYOUT = os.path.join(REPO, "data", "world", "layout.json")
TERRAIN = os.path.join(REPO, "data", "world", "terrain")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def points(faces):
    return [q for f in faces for q in f.pts]


def tris(faces):
    return sum(len(f.pts) - 2 for f in faces)


class PitchPieceTests(unittest.TestCase):
    def test_catalogue_builds_and_palette_slots_exist(self):
        slots = {tuple(s) for s in load(S.TEXTURES_PATH)["props_slots"] if isinstance(s, list)}
        for zone, name in PP.ZONES.items():
            self.assertIn((PP.TILE[zone], "@" + name), slots, name)
        for name in PP.BUILDERS:
            p = P.build(name, **P.CATALOGUE[name])
            self.assertGreater(p.triangles(), 0, name)
            for f in p.faces + (p.lod1 or []):
                self.assertNotIn("#", f.tag, name)  # palette names only, never a literal colour
                mat = G.parse_face_tag(f.tag).material
                self.assertIn(mat, ("castle_props", "castle_wood", "castle_roof", "castle_cloth", "castle_floor"), name)

    def test_hoop_ring_and_caps(self):
        for h, r in ((13.5, 1.3), (16.5, 1.3), (8.0, 1.3)):
            p = P.build("kit_pitch_hoop", height=h, radius=r)
            self.assertLessEqual(p.triangles(), PP.HOOP_TRIS)
            self.assertLess(tris(p.lod1), p.triangles())
            pts = points(p.faces)
            top = max(q[1] for q in pts)
            self.assertAlmostEqual(top, h + r + 0.075, places=3)
            ring = [q for q in pts if q[1] > h - r * 0.5]  # the ring, above the pole's collar
            self.assertLessEqual(max(abs(q[2]) for q in ring), 0.08)  # the ring faces along z
            self.assertTrue(p.walkthrough)
        with self.assertRaises(ValueError):
            P.build("kit_pitch_hoop", height=40.0)

    def test_tower_inside_its_no_fly_reach(self):
        for house in range(4):
            p = P.build("kit_pitch_tower", house=house, size=4.0, height=20.0)
            self.assertLessEqual(p.triangles(), PP.TOWER_TRIS)
            self.assertLess(tris(p.lod1), p.triangles())
            pts = points(p.faces)
            self.assertLessEqual(max(q[1] for q in pts), 20.0 + PP.TOWER_APEX + 1e-9)
            self.assertLessEqual(max(max(abs(q[0]), abs(q[2])) for q in pts), 2.0 + 0.5 - 1e-3)
            cloth = {f.tag for f in p.faces if f.tag.startswith("castle_cloth")}
            self.assertEqual(cloth, {f"castle_cloth|fit:{PP.BANNERS[house]}"})
            fabric = {f.tag for f in p.faces if ":fabric:" in f.tag}
            self.assertEqual(fabric, {G.palette_tag("fabric", f"house_{'abcd'[house]}")})
        timber = P.build("kit_pitch_tower", house=1, slates=0)
        self.assertFalse(any(f.tag.startswith("castle_roof") for f in timber.faces + timber.lod1))

    def test_stand_deck_top_and_roof_reach(self):
        p = P.build("kit_pitch_stand", length=66.0, depth=5.0, deck=2.4, roof=3.8, steps=4.0, steps_at=1)
        self.assertLessEqual(p.triangles(), PP.STAND_TRIS_PER_M * 66)
        self.assertLess(tris(p.lod1), p.triangles())
        # The deck's top covers the deck at exactly y = deck.
        top = [f for f in p.faces if f.normal()[1] > 0.99 and all(abs(q[1] - 2.4) < 1e-9 for q in f.pts)
               and all(-1e-9 <= q[0] <= 66.0 + 1e-9 and -5.0 - 1e-9 <= q[2] <= 1e-9 for q in f.pts)]
        self.assertAlmostEqual(sum(f.area() for f in top), 66.0 * 5.0, places=6)
        pts = points(p.faces)
        self.assertLessEqual(max(q[2] for q in pts), 0.5)  # the roof's eave inside the no-fly box (0.5 m)
        self.assertGreaterEqual(min(q[2] for q in pts), -5.0 - 0.5)
        self.assertLessEqual(max(q[1] for q in pts), 2.4 + 3.8 + PP.STAND_ROOF_RISE + 1e-9)
        self.assertGreaterEqual(min(q[0] for q in pts), -0.5)
        self.assertLessEqual(max(q[0] for q in pts), 66.0 + 4.0 + 0.5)
        # The treads climb to the deck, each within half a riser of the layout's ramp at its middle.
        treads = [f for f in p.faces if f.tag == PP.PLANKS and f.normal()[1] > 0.99 and min(q[0] for q in f.pts) >= 66.0 - 1e-9]
        self.assertGreaterEqual(len(treads), 10)
        for f in treads:
            xm = sum(q[0] for q in f.pts) / len(f.pts)
            ramp = 2.4 * (70.0 - xm) / 4.0
            self.assertLess(abs(f.pts[0][1] - ramp), 0.11)
        west = P.build("kit_pitch_stand", length=20.0, steps_at=-1)
        self.assertLess(min(q[0] for q in points(west.faces)), -3.9)

    def test_gate_and_apron(self):
        g = P.build("kit_pitch_gate")
        self.assertLessEqual(g.triangles(), PP.GATE_TRIS)
        fabric = {f.tag for f in g.faces if ":fabric:" in f.tag}
        self.assertEqual(fabric, {G.palette_tag("fabric", f"house_{h}") for h in "abcd"})
        a = P.build("kit_pitch_apron", x0=-3.0, z0=-2.0, x1=3.0, z1=2.0, inner=0.5, outer=5.0, drop=0.05)
        self.assertEqual(len(a.faces), 4)
        self.assertTrue(all(abs(q[1] + 0.05) < 1e-9 for q in points(a.faces)))
        for x, z in ((0.0, 0.0), (3.2, 0.0), (0.0, -2.3)):  # nothing over the room or its wall
            self.assertFalse(any(L_inside(f.pts, x, z) for f in a.faces), (x, z))

    def test_no_colour_literals(self):
        for path in (os.path.join(BLENDER, "castle_kit", "pieces", "pitch.py"),
                     os.path.join(BLENDER, "world", "pitch_build.py"), os.path.join(BLENDER, "world", "pitch_export.py"),
                     os.path.join(BLENDER, "world", "pitch_stills.py"),
                     os.path.join(BLENDER, "world", "school_rooms_pitch.py"),
                     os.path.join(BLENDER, "icons", "icon_models_flying.py")):
            with open(path, encoding="utf-8") as f:
                self.assertIsNone(HEX.search(f.read()), path)


def L_inside(pts, x, z) -> bool:
    """Whether (x, z) lies inside a convex face's plan (pts CCW or CW)."""
    sign = 0
    n = len(pts)
    for i in range(n):
        (ax, _, az), (bx, _, bz) = pts[i], pts[(i + 1) % n]
        c = (bx - ax) * (z - az) - (bz - az) * (x - ax)
        if abs(c) < 1e-12:
            continue
        s = 1 if c > 0 else -1
        if sign and s != sign:
            return False
        sign = s
    return True


class PitchCellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = L.load_layout(LAYOUT)
        cls.textures = S.load_texture_set()
        cls.meta = load(os.path.join(TERRAIN, "grounds.json"))["pitch"]
        cls.pt = PB.build(cls.layout, TERRAIN, cls.textures)
        cls.errs, cls.warns, cls.rep = PB.lint(cls.pt, cls.layout, TERRAIN)

    def test_lint_clean_within_budget(self):
        self.assertEqual(self.errs, [])
        self.assertLessEqual(self.pt.draws(), G.P3B_PITCH_BUDGET.draws)
        self.assertLessEqual(self.pt.tris(), G.P3B_PITCH_BUDGET.tris[1])
        self.assertLessEqual(self.pt.lod1_tris(), G.P3B_PITCH_LOD1_TRIS)
        self.assertEqual(self.pt.cell, G.P3B_PITCH_CELL)
        self.assertEqual(set(self.pt.lod_meshes), {n + PB.LOD1_SUFFIX for n in self.pt.meshes})
        self.assertEqual(self.pt.library(), ["castle_cloth", "castle_floor_grass", "castle_props", "castle_roof",
                                             "castle_wood"])

    def test_deterministic(self):
        again = PB.build(self.layout, TERRAIN, self.textures)
        self.assertEqual(again.digest(), self.pt.digest())

    def test_pieces_from_the_contract(self):
        by = {p.id: p for p in self.pt.placed}
        for h in self.meta["hoops"]:
            p = by[h["id"]]
            self.assertEqual(p.piece, "kit_pitch_hoop")
            self.assertAlmostEqual(p.pos[1] + p.args["height"], h["pos"][1])
            self.assertEqual((p.pos[0], p.pos[2]), (h["pos"][0], h["pos"][2]))
            self.assertAlmostEqual(p.yaw, h["yaw"])
        for t in self.meta["towers"]:
            p = by[t["id"]]
            self.assertEqual("abcd"[p.args["house"]], t["house"][-1])
            self.assertAlmostEqual(p.pos[1] + p.args["height"], t["top"])
        self.assertEqual(by["pitch_gate"].pos, tuple(self.meta["gate"]["prop"]))
        self.assertEqual(sum(1 for p in self.pt.placed if p.piece == "kit_pitch_stand"), 2)

    def test_turf_and_lines_on_the_terrace(self):
        y = self.meta["y"]
        cx, cz, a, b = PB._ellipse(self.meta)
        turf = [f for f in self.pt.faces if f.src.startswith("turf")]
        lines = [f for f in self.pt.faces if f.src == "lines"]
        self.assertTrue(turf and lines)
        for f in turf + lines:
            for x, fy, z in f.pts:
                self.assertTrue(0.0 < fy - y <= 0.02 + 1e-9)
                self.assertLessEqual(((x - cx) / a) ** 2 + ((z - cz) / b) ** 2, 1.0 + 1e-6)
        self.assertAlmostEqual(sum(f.area() for f in turf), math.pi * a * b, delta=0.01 * math.pi * a * b)
        # The stripes alternate two greys; the lines are palette white on the props atlas.
        greys = {round(c[0], 4) for f, cols in zip(self.pt.meshes["pitch.castle_floor_grass"].src_faces,
                                                   self.pt.meshes["pitch.castle_floor_grass"].colors) for c in cols}
        self.assertEqual(greys, set(PB.STRIPE_GREYS))
        self.assertTrue(all(f.material == "castle_props" and f.arg == "fabric:@letter_white" for f in lines))

    def test_seats_on_the_decks_facing_the_field(self):
        decks = {s.id: s for s in self.layout.surfaces if s.id in ("pitch_stand_n", "pitch_stand_s")}
        self.assertEqual(len(self.pt.seats), 2 * len(PB.BENCH_ROWS) * int(66 // PB.BENCH_PITCH))
        for x0, z0, x1, z1, y, yaw, front, back, drop in self.pt.seats:
            self.assertEqual((front, back, drop), (HF.BENCH_DEPTH / 2, HF.BENCH_DEPTH / 2, HF.BENCH_SEAT))
            s = decks["pitch_stand_n"] if z1 < 200 else decks["pitch_stand_s"]
            self.assertTrue(s.x0 <= x0 < x1 <= s.x1 and s.z0 <= z0 < z1 <= s.z1)
            self.assertAlmostEqual(y, s.y + HF.BENCH_SEAT)
            self.assertAlmostEqual(yaw, 0.0 if s.id == "pitch_stand_n" else math.pi, places=6)

    def test_export_hook_found(self):
        mod = outdoor.optional_module("pitch_export")
        self.assertIsNotNone(mod)
        self.assertTrue(callable(mod.build))


class ClassPitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.room = class_rooms.build_all()["class_pitch"]
        cls.cb, cls.b = IK.build(cls.room)
        cls.rep = IK.lint(cls.room, (cls.cb, cls.b))

    def test_contract_kept(self):
        self.assertEqual(self.room.layout, PC.contract_layout())
        self.assertEqual(PC.missing(load(PC.PATH)), [])
        self.assertEqual(G.m5b_rules("class_pitch"), "upper_rooms")

    def test_lint_clean_classroom_budget_no_slates(self):
        self.assertEqual(self.rep.errors, [])
        self.assertLessEqual(self.cb.draws(), G.P3B_CLASS_PITCH_DRAWS)
        self.assertNotIn("castle_roof", {m.material for m in self.cb.meshes.values()})

    def test_pitch_pieces_round_the_field_clear_of_the_course(self):
        hero = {h["id"]: h for h in self.room.spec["hero"]}
        houses = sorted(h["args"]["house"] for h in hero.values() if h["piece"] == "kit_pitch_tower")
        self.assertEqual(houses, [0, 1, 2, 3])
        rings = PC.rings()
        for h in hero.values():
            if h["piece"] == "kit_pitch_hoop":
                x, _, z = h["pos"]
                for r in rings:
                    self.assertGreater(math.hypot(x - r["pos"][0], z - r["pos"][2]), r["radius"] + 2.0)
            if h["piece"] in ("kit_pitch_tower", "kit_pitch_stand", "kit_tree_round"):
                x, _, z = h["pos"]
                self.assertFalse(PC.FLOOR[0] - 1.0 < x < PC.FLOOR[2] + 1.0 and PC.FLOOR[1] - 1.0 < z < PC.FLOOR[3] + 1.0,
                                 h["id"])

    def test_ring_prop(self):
        self.assertIn("ring_hoop", class_rooms.CLASS_PROPS["class_pitch"])
        m = SRP.ring_mesh()
        self.assertLessEqual(m.triangle_count(), 600)
        lo, hi = m.bounds()
        r, t = SRP.RING["radius"], SRP.RING["tube"]
        self.assertAlmostEqual(hi[0], r + t, places=6)
        self.assertAlmostEqual(lo[1], -(r + t), places=6)
        self.assertLessEqual(max(abs(lo[2]), abs(hi[2])), t + 1e-9)  # a 1 m ring facing +z
        self.assertEqual(m.materials(), ["metal"])


class FlyingIconTests(unittest.TestCase):
    def test_flying_icons_models_and_palette(self):
        ids = {c["id"] for c in load(os.path.join(REPO, "data", "items", "palette.json"))["colours"]}
        self.assertEqual(set(icon_models.FLYING_ICONS), {"ring", "broom"})
        self.assertFalse(set(icon_models.FLYING_ICONS) & set(icon_models.CLASS_ICONS))
        for name, (_, dyes) in icon_models.FLYING_ICONS.items():
            m = icon_models.build_class_icon(name)
            lo, hi = m.bounds()
            self.assertAlmostEqual(lo[1], 0.0, places=6)
            self.assertLess(max(hi[k] - lo[k] for k in range(3)), 0.5, name)
            self.assertTrue(set(dyes) <= ids, name)

    def test_featherlight_bottle_has_its_feather(self):
        item = {"id": 3313, "potion": {"effect": "featherlight"}, "asset": "icons/potion/featherlight"}
        m = icon_models.potion_bottle(item)
        top = icon_models.POTION_SHAPES["featherlight"][-1][1]
        self.assertGreater(m.bounds()[1][1], top + 0.08)
        self.assertLessEqual(m.triangle_count(), 1500)


if __name__ == "__main__":
    unittest.main()
