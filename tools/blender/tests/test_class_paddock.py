"""Unit tests for the Creature Keeping room (Paddock
builder, docs/architecture.md): `class_paddock`, an outdoor
paddock open to the sky (world/school_rooms_paddock.py), its pieces
(castle_kit/pieces/paddock.py), its turf (castle_floor_grass), the two
creature props (world/beast_props.py), the two grounds gates
(world/kit_models.py `garden_gate`, `field_gate`, data/world/props.json)
and the Creatures class icons (icons/creature_icons.py). The generic
class-room checks (lint, budget, contract ids, the door behind the exit,
determinism, palette lights) are test_class_rooms.py's, which covers this
room too. Pure Python; run through tests/run_tests.py."""

import json
import math
import os
import re
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.path.dirname(HERE)
REPO = os.path.abspath(os.path.join(BLENDER, "..", ".."))
sys.path[:0] = [os.path.join(BLENDER, "world"), os.path.join(BLENDER, "icons"), BLENDER]

import beast_props as BP  # noqa: E402
import class_rooms  # noqa: E402
import creature_icons  # noqa: E402
import icon_models  # noqa: E402
import instance_kit as IK  # noqa: E402
import kit  # noqa: E402
import kit_models as km  # noqa: E402
import layout_lib as L  # noqa: E402
import paint  # noqa: E402
import palette  # noqa: E402
import school_rooms_paddock as SP  # noqa: E402
import foliage as FO  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import pieces as P  # noqa: E402
from castle_kit import props_atlas  # noqa: E402
from castle_kit import schema as S  # noqa: E402
from castle_kit import textures as T  # noqa: E402
from castle_kit.pieces import paddock as PP  # noqa: E402

LID = "class_paddock"
HEX = re.compile(r"#[0-9A-Fa-f]{6}\b")
LAYOUT = os.path.join(REPO, "data", "world", "layout.json")
PROPS = os.path.join(REPO, "data", "world", "props.json")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def piece_points(p):
    return [q for f in p.faces for q in f.pts]


class PaddockRoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.room = class_rooms.build_all()[LID]
        cls.cb, cls.b = IK.build(cls.room)
        cls.rep = IK.lint(cls.room, (cls.cb, cls.b))
        cls.hero = {h["id"]: h for h in cls.room.spec["hero"]}
        sched = load(os.path.join(REPO, "data", "classes", "schedule.json"))
        cls.cap = max(c["instance_cap"] for c in sched["classes"] if c["layout"] == LID)
        cls.marks = {m["id"]: m["pos"] for m in cls.room.layout["landmarks"]}

    def test_merged_lint_clean_in_budget(self):
        self.assertIs(class_rooms.KIT_ROOMS[LID], SP.class_paddock)
        self.assertEqual(G.m5b_rules(LID), "upper_rooms")
        self.assertEqual(self.rep.errors, [])
        self.assertLessEqual(self.cb.draws(), G.BUDGETS["classroom"].draws)
        self.assertLessEqual(self.cb.triangles(), G.BUDGETS["classroom"].tris[1])

    def test_open_to_the_sky_on_grass(self):
        lay = L.parse_layout(self.room.layout)
        (s,) = lay.surfaces
        self.assertEqual((s.id, s.kind, s.ceiling), ("class_floor", "grounds", None))
        self.assertFalse(s.roofed)
        mats = {m.material for m in self.cb.meshes.values()}
        self.assertNotIn("castle_ceiling", mats)
        self.assertIn("castle_floor_grass", mats)
        self.assertEqual(self.room.spec["materials"]["castle_floor"], "castle_floor_grass")
        self.assertFalse(self.room.spec["floor_border"])
        # Nothing of stone, trim or ceiling over the floor above head height but the gatehouse on the south edge.
        for f in self.cb.faces:
            if not f.material.startswith(("castle_wall", "castle_trim", "castle_ceiling")):
                continue
            if f.src.startswith(("blind_door:", "hero:gatehouse", "hero:lantern_door")):
                continue
            over = [p for p in f.pts if s.x0 + 0.2 < p[0] < s.x1 - 0.2 and s.z0 + 0.2 < p[2] < s.z1 - 0.2]
            self.assertFalse(any(p[1] > 2.6 for p in over), f"{f.src} roofs the paddock")

    def test_the_props_landmarks(self):
        props = class_rooms.CLASS_PROPS[LID]
        self.assertEqual(set(props), {"beast_puff", "beast_lynxkit"})
        self.assertEqual(self.marks["beast_puff"], [-2.0, 0.0, -3.0])
        self.assertEqual(self.marks["beast_lynxkit"], [2.0, 0.0, -3.0])
        x0, z0, x1, z1 = SP.PEN
        for pid in props:
            x, _, z = self.marks[pid]
            self.assertTrue(x0 + 1.0 <= x <= x1 - 1.0 and z0 + 1.0 <= z <= z1 - 1.0, pid)  # inside the pen, clear of its rails
        # The professor stands outside the pen, behind it, facing the class over it.
        px, _, pz = self.marks["class_professor"]
        self.assertLess(pz, z0 - 0.6)

    def test_class_door_in_the_gatehouse(self):
        (door,) = [o for o in self.cb.plan.openings if o.kind == "blind_door"]
        self.assertEqual(door.piece, G.CLASS_DOOR)
        self.assertEqual((door.t0, door.t1, door.c, door.sign), (-1.5, 1.5, SP.Z1, -1.0))
        gate = self.hero["gatehouse"]
        self.assertEqual((gate["piece"], gate["pos"]), (G.STAIR_HEAD, [0.0, 0.0, SP.Z1]))
        self.assertEqual(self.room.head_door["span"], [-3.0, 3.0])

    def test_a_full_section_of_flat_standing_marks(self):
        marks = [h for h in self.hero.values() if h["piece"] == "kit_stand_mark"]
        self.assertGreaterEqual(len(marks), self.cap)
        ex = self.room.layout["interactables"][0]["pos"]
        x0, z0, x1, z1 = SP.PEN
        for h in marks:
            x, y, z = h["pos"]
            self.assertTrue(SP.X0 < x < SP.X1 and SP.Z0 < z < SP.Z1, h["id"])
            self.assertFalse(x0 <= x <= x1 and z0 <= z <= z1, h["id"])  # never in the pen
            self.assertGreater(z, z1 + 1.0)  # in front of the pen, facing it and the professor
            self.assertGreater(math.dist((x, z), (ex[0], ex[2])), 2.0)
            p = P.build("kit_stand_mark", **h["args"])
            self.assertLessEqual(max(q[1] for q in piece_points(p)), PP.STAND_MARK_H + 1e-9)
        pitch = min(math.dist((a["pos"][0], a["pos"][2]), (b["pos"][0], b["pos"][2])) for a in marks for b in marks if a is not b)
        self.assertGreaterEqual(pitch, 1.5)  # room for a student each

    def test_fenced_all_round(self):
        """The fence on the low wall's top covers every side but the
        gatehouse's span; nothing of it enters the floor."""
        spans = {"n": [], "s": [], "e": [], "w": []}
        for hid, h in self.hero.items():
            if not hid.startswith("fence_"):
                continue
            x, y, z = h["pos"]
            self.assertAlmostEqual(y, SP.LOW_WALL)
            yaw, length = h["yaw"], h["args"]["length"]
            ux, uz = math.cos(yaw), -math.sin(yaw)
            if abs(uz) < 0.5:
                side = "n" if z < 0 else "s"
                spans[side].append(tuple(sorted((x, x + ux * length))))
                self.assertGreater(abs(z), SP.Z1)
            else:
                side = "w" if x < 0 else "e"
                spans[side].append(tuple(sorted((z, z + uz * length))))
                self.assertGreater(abs(x), SP.X1)
        half = SP.GATEHOUSE["width"] / 2.0
        spans["s"].append((-half, half))
        for side, (lo, hi) in (("n", (SP.X0, SP.X1)), ("s", (SP.X0, SP.X1)), ("e", (SP.Z0, SP.Z1)), ("w", (SP.Z0, SP.Z1))):
            t = lo
            for a, b in sorted(spans[side]):
                self.assertLessEqual(a, t + 0.2, f"{side}: a gap at {t}")
                t = max(t, b)
            self.assertGreaterEqual(t, hi - 0.2, side)

    def test_outside_the_wall(self):
        """Hedges, trees and the hut stand outside the floor (they close the
        view); the hut's lit side faces the paddock."""
        for h in self.hero.values():
            if h["piece"] in ("kit_hedge_run", "kit_tree_round", "kit_hut_corner"):
                x, _, z = h["pos"]
                self.assertTrue(abs(x) > SP.X1 + 0.5 or abs(z) > SP.Z1 + 0.5, h["id"])
        hut = self.hero["hut"]
        yaw = hut["yaw"]
        front = (math.sin(yaw), math.cos(yaw))
        to_room = (-hut["pos"][0], -hut["pos"][2])
        self.assertGreater(front[0] * to_room[0] + front[1] * to_room[1], 0.9 * math.hypot(*to_room))

    def test_lights_and_no_colour_literals(self):
        self.assertTrue(1 <= len(self.room.lights) <= 6)
        for lt in self.room.lights:
            self.assertIn(lt["color"], set(palette.GLOW.values()))
        self.assertEqual(self.room.lights[0]["color"], palette.GLOW["sun"])
        for path in (SP.__file__, PP.__file__, BP.__file__, creature_icons.__file__):
            with open(path, encoding="utf-8") as f:
                self.assertIsNone(HEX.search(f.read()), path)


class PaddockPieceTests(unittest.TestCase):
    def test_palette_slots(self):
        slots = {tuple(s) for s in load(S.TEXTURES_PATH)["props_slots"] if isinstance(s, list)}
        for zone, name in PP.ZONES.items():
            self.assertIn((PP.TILE[zone], "@" + name), slots, name)

    def test_in_the_catalogue(self):
        for name in PP.BUILDERS:
            self.assertIn(name, P.CATALOGUE)
            p = P.build(name, **P.CATALOGUE[name])
            self.assertGreater(p.triangles() + len(p.meta.get("foliage", [])), 0)

    def test_fence(self):
        for length in (1.0, 6.0, 20.5):
            for rails in (2, 3):
                p = P.build("kit_fence_rail", length=length, height=1.0, rails=rails)
                pts = piece_points(p)
                self.assertTrue(p.walkthrough)
                self.assertLessEqual(p.triangles(), PP.FENCE_TRIS_PER_M * max(2.0, length))
                self.assertAlmostEqual(min(q[1] for q in pts), 0.0)
                self.assertAlmostEqual(max(q[1] for q in pts), 1.0)
                self.assertGreaterEqual(min(q[0] for q in pts), -0.07)
                self.assertLessEqual(max(q[0] for q in pts), length + 0.07)
        for bad in ({"height": 2.0}, {"rails": 4}, {"length": 0.1}):
            with self.assertRaises(ValueError):
                P.build("kit_fence_rail", **bad)

    def test_hedge_tree_hut(self):
        # Vegetation look A: the hedge and the trees are plantings of the foliage kit (world/foliage.py).
        h = P.build("kit_hedge_run", length=12.0, height=2.6, seed=3)
        self.assertEqual(h.triangles(), 0)
        plants = h.meta["foliage"]
        self.assertLessEqual(FO.lod0_tris(plants), FO.HEDGE_TRIS_PER_M * 12)
        tops = [p.point((0.0, FO.SPECIES[p.species].height, 0.0))[1] for p in plants]
        self.assertAlmostEqual(max(tops), 2.6, places=6)
        ends = [p.point((x, 0.0, 0.0))[0] for p in plants for x in ((-FO.hedge_cap(p.species), 0.0) if FO.SPECIES[p.species].kind == "hedge_end" else (0.0, FO.HEDGE_SEG))]
        self.assertAlmostEqual(min(ends), 0.0, places=6)
        self.assertAlmostEqual(max(ends), 12.0, places=6)
        for seed in range(1, 10):
            t = P.build("kit_tree_round", height=6.0, seed=seed)
            (pl,) = t.meta["foliage"]
            self.assertAlmostEqual(pl.t[1], 0.0, places=6)
            self.assertAlmostEqual(pl.scale() * FO.species_top(pl.species), 6.0, places=4)
        hut = P.build("kit_hut_corner")
        self.assertLessEqual(hut.triangles(), PP.HUT_TRIS)
        self.assertTrue(any(f.tag == PP.WALL for f in hut.faces))
        # Half a hut: nothing behind its cut (local z < 0) but the chimney's base and the eaves' edge.
        self.assertGreaterEqual(min(q[2] for q in piece_points(hut)), -0.05)
        self.assertLessEqual(P.build("kit_hay_bale").triangles(), PP.HAY_BALE_TRIS)
        self.assertLessEqual(P.build("kit_trough").triangles(), PP.TROUGH_TRIS)
        self.assertLessEqual(P.build("kit_stand_mark", seed=4).triangles(), PP.STAND_MARK_TRIS)

    def test_deterministic(self):
        for name, args in P.CATALOGUE.items():
            if name in PP.BUILDERS:
                a, b = P.build(name, **args), P.build(name, **args)
                self.assertEqual([f.pts for f in a.faces], [f.pts for f in b.faces], name)


class TurfTextureTests(unittest.TestCase):
    """castle_floor_grass (grass_turf, family castle_floor)."""

    @classmethod
    def setUpClass(cls):
        cls.spec = load(S.TEXTURES_PATH)
        cls.img = T.PAINTERS["grass_turf"](cls.spec)

    def test_listed(self):
        m = self.spec["materials"]["castle_floor_grass"]
        self.assertEqual((m["texture"], m["family"], m["milestone"], m["px"], m["map"], m["tile_m"]),
                         ("grass_turf", "castle_floor", 7, [1024, 1024], "world", 4.8))
        self.assertLessEqual(m["milestone"], T.MILESTONE)
        self.assertEqual(self.img.shape, (1024, 1024, 4))

    def test_green_tiling_deterministic(self):
        lum = paint.luminance(self.img)
        self.assertTrue(0.2 <= lum.mean() <= 0.6, lum.mean())
        self.assertGreater(lum.min(), 0.04)
        r, g, b = self.img[..., :3].reshape(-1, 3).mean(axis=0)
        self.assertGreater(g, r)
        self.assertGreater(r, b)
        for axis in (0, 1):
            edge = np.abs(np.take(self.img, 0, axis=axis) - np.take(self.img, -1, axis=axis)).mean()
            inner = np.abs(np.take(self.img, 511, axis=axis) - np.take(self.img, 512, axis=axis)).mean()
            self.assertLess(edge, inner * 3 + 0.02)
        self.assertTrue(np.array_equal(T.PAINTERS["grass_turf"](self.spec), self.img))


class BeastPropTests(unittest.TestCase):
    def test_two_nodes_in_budget_palette_named(self):
        names = props_atlas.palette()
        for pid in ("beast_puff", "beast_lynxkit"):
            b = BP.build(pid)
            self.assertEqual(b.pid, pid)
            self.assertEqual(len(b.parts), BP.MAX_DRAWS)  # body and one child, one material: two draws
            self.assertEqual(b.parts[0].name, pid)
            self.assertLessEqual(b.triangles(), BP.MAX_TRIS)
            self.assertTrue(set(b.zones.values()) <= set(names), pid)
            self.assertLessEqual(len(b.zones), BP.SWATCH_GRID ** 2)
            for part in b.parts:
                self.assertEqual(part.mesh.materials(), ["fur"])
                self.assertTrue(part.mesh.zones() <= set(b.zones), part.name)
            body = b.parts[0].mesh
            lo, hi = body.bounds()
            self.assertAlmostEqual(lo[1], 0.0, places=6)  # the origin at its base
            self.assertLess(hi[1], 1.0)
            self.assertGreater(hi[2], -lo[2] * 0.8)  # the face toward +z
            child = b.parts[1]
            clo, _ = child.mesh.bounds()
            self.assertGreater(child.origin[1] + clo[1], 0.0)  # off the ground
            again = BP.build(pid)
            self.assertEqual([p.mesh.verts for p in again.parts], [p.mesh.verts for p in b.parts])

    def test_swatch_uvs(self):
        b = BP.build("beast_lynxkit")
        uvs = {BP.zone_uv(b, z) for z in b.zones}
        self.assertEqual(len(uvs), len(b.zones))
        for u, v in uvs:
            self.assertTrue(0.0 < u < 1.0 and 0.0 < v < 1.0)
        self.assertEqual(len(BP.swatch_colours(b, props_atlas.palette())), len(b.zones))


class GroundsGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = load(LAYOUT)
        cls.kit = {k.id: k for k in kit.load_kit(PROPS)}

    def test_models(self):
        for name in ("garden_gate", "field_gate"):
            k = km.build(name)
            mats = set(k.mesh.materials())
            self.assertTrue(all(m.startswith("g:") and m[2:] in L.MATERIALS for m in mats), name)
            self.assertLessEqual(len(mats), 2, name)  # one or two draws in its grounds cell
            self.assertLessEqual(k.mesh.triangle_count(), km.budget(name))
            lo, hi = k.mesh.bounds()
            self.assertAlmostEqual(lo[1], -km.GATE_SINK)
            self.assertLess(hi[1], 2.0)
            self.assertLess(max(-lo[0], hi[0]), 5.0)
            self.assertEqual(k.lights, [])

    def test_placed_at_the_gates(self):
        marks = {m["id"]: m for m in self.layout["landmarks"]}
        doors = {i["id"]: i for i in self.layout["interactables"]}
        # Moved the paddock gate beside the Groundskeeper's Cottage (tools/blender/terrain_layout.py).
        for kid, model, cell, mark, door in (("gate_greenhouses", "garden_gate", "grounds_r0_c3", "greenhouses", "greenhouse_gate"),
                                              ("gate_paddock", "field_gate", "grounds_r2_c5", "paddock", "paddock_gate")):
            k = self.kit[kid]
            self.assertEqual((k.model, k.cell), (model, cell))
            self.assertLess(math.dist(k.pos, marks[mark]["pos"]), 0.05)
            self.assertAlmostEqual(k.yaw, math.pi, places=3)  # facing the castle (-z)
            self.assertEqual(doors[door]["kind"], "class_door")
            d = math.dist((k.pos[0], k.pos[2]), (doors[door]["pos"][0], doors[door]["pos"][2]))
            self.assertLessEqual(d, doors[door]["radius"])  # the player meets the gate inside the door's reach


class CreatureIconTests(unittest.TestCase):
    def test_own_models(self):
        keys = [n for n in icon_models.CLASS_ICONS if n.startswith(("need_", "care_"))]
        self.assertEqual(len(keys), 15)
        self.assertEqual(set(keys), set(creature_icons.CREATURE_ICONS))
        for n in keys:
            fn, _ = icon_models.CLASS_ICONS[n]
            self.assertEqual(fn.__module__, "creature_icons", n)
            self.assertTrue(icon_models.build_class_icon(n).zones() <= {0, 1, 2, 3})


if __name__ == "__main__":
    unittest.main()
