"""Playtest fixes (docs/architecture.md), the world part: a drawn floor
under every walkable surface (the Grand Staircase's pier landing at 13.5 m had none;
the Dueling Club's steps showed the floor beside their runner), the ground meeting
the castle's outer walls (the terrain's castle lip), the far silhouette closed (the
ranges under the seventh-floor corridor and round the west court, the courtyard's
paving in its stand-in) and the common rooms' entrances as moving nodes.
"""

import json
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.path.dirname(HERE)
sys.path[:0] = [BLENDER, os.path.join(BLENDER, "world")]

import grounds_terrain as GT  # noqa: E402
import hlod_build as HB  # noqa: E402
import layout_lib as L  # noqa: E402
from castle_kit import assemble as A  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import lint as LN  # noqa: E402
from castle_kit import rules as R  # noqa: E402
from castle_kit import schema as S  # noqa: E402

REPO = os.path.dirname(os.path.dirname(BLENDER))
TERRAIN = os.path.join(REPO, "data", "world", "terrain")
EXTERIOR = os.path.join(REPO, "data", "world", "exterior.json")
# The common rooms' entrances: (door, the hero's cell, style).
HOUSE_ENTRANCES = (
    ("cr_a_portrait", "l7_corridor_north", "painting"),
    ("cr_c_door", "west_tower_stair", "double"),  # Knocker door opens as a pair (was painting)
    ("cr_d_wall", "dungeon_west", "wall"),
    ("cr_b_barrels", "basement_east", "panel"),
)
_B: dict = {}


def builder() -> A.Builder:
    if "b" not in _B:
        _B["b"] = A.Builder.from_files()
    return _B["b"]


def build(cell: str) -> A.CellBuild:
    key = f"cb:{cell}"
    if key not in _B:
        _B[key] = builder().build(cell)
    return _B[key]


class FloorUnderTests(unittest.TestCase):
    def test_every_walkable_surface_has_a_drawn_floor(self):
        b = builder()
        bad = {}
        for cell in sorted(b.kit_plan.cells):
            misses = LN.floor_under_misses(build(cell), b.layout)
            if misses:
                bad[cell] = {sid: pts[:3] for sid, pts in misses.items()}
        self.assertEqual(bad, {})

    def test_the_pier_landing_has_its_floor(self):
        """Its floor like the landings at 31.5 and 49.5 (the gallery edges, no floor border)."""
        cb = build("grand_staircase")
        s = builder().layout.by_id("gs_pier_1_2")
        floors = [f for f in cb.faces if f.src == "floor" and all(abs(p[1] - s.y) < 0.05 for p in f.pts)]
        self.assertTrue(floors)
        area = sum(f.area() for f in floors if f.normal()[1] > 0.99
                   and all(s.x0 - 1e-6 <= p[0] <= s.x1 + 1e-6 and s.z0 - 1e-6 <= p[2] <= s.z1 + 1e-6 for p in f.pts))
        self.assertAlmostEqual(area, (s.x1 - s.x0) * (s.z1 - s.z0), delta=0.5)
        # The ground floor round the pier keeps the compass's cut.
        self.assertEqual(LN.floor_under_misses(cb, builder().layout), {})

    def test_the_check_finds_a_missing_floor(self):
        cb = build("grand_staircase")
        kept = list(cb.faces)
        try:
            cb.faces[:] = [f for f in kept if not (f.src in ("floor", "floor_border:gs_pier_1_2")
                                                   and min(p[1] for p in f.pts) > 13.0)]
            self.assertIn("gs_pier_1_2", LN.floor_under_misses(cb, builder().layout))
        finally:
            cb.faces[:] = kept

    def test_duel_steps_full_width(self):
        cb = build("great_hall")
        lay = builder().layout
        misses = LN.floor_under_misses(cb, lay)
        self.assertNotIn("gh_duel_steps_east", misses)
        self.assertNotIn("gh_duel_steps_west", misses)


class CastleLipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = L.load_layout(S.LAYOUT_PATH)
        cls.g = GT.Grounds.load(TERRAIN, cls.layout)
        cls.pt = GT.Painter(cls.g)

    def test_the_gaps_are_the_castle_floors_crossing_the_strip(self):
        self.assertEqual(self.g.lip_gaps, [(12.0, 20.0), (101.0, 107.0)])  # the courtyard gate, the covered bridge

    def test_the_ground_runs_under_the_outer_walls(self):
        edge = self.g.meta["box"]["min"][1]
        far = edge - GT.CASTLE_LIP
        for cell in ("grounds_r0_c0", "grounds_r0_c1", "grounds_r0_c2", "grounds_r0_c3", "grounds_r0_c4", "grounds_r0_c5"):
            for m in (GT.terrain_mesh(self.g, cell, self.pt), GT.terrain_lod1(self.g, cell, self.pt)):
                lip = {(round(v[0], 3), round(v[1], 3)) for k, v in enumerate(m.verts)
                       if k not in m.skirt and abs(v[2] - far) < 1e-6}
                self.assertTrue(lip, cell)
                for x, y in lip:
                    self.assertAlmostEqual(y, round(self.g.height(x, edge), 4), places=3)
                    self.assertFalse(any(x0 < x < x1 for x0, x1 in self.g.lip_gaps), (cell, x))
                # The skirt hangs from the lip's far edge where there is one.
                skirt_z = {round(v[2], 3) for k, v in enumerate(m.verts) if k in m.skirt}
                self.assertIn(round(far, 3), skirt_z, cell)

    def test_the_courtyard_wall_stands_on_the_lip(self):
        # The courtyard's south wall's outer face (the Planner's, z 91) lies over the lip.
        plan = HB.layout_plan(self.layout)
        faces = [p for p in plan.polys[("courtyard", "wall_exterior")] if all(abs(q[2] - 91.0) < 1e-6 for q in p)]
        self.assertTrue(faces)
        self.assertLess(self.g.meta["box"]["min"][1] - GT.CASTLE_LIP, 91.0)


def _hlod_tris(exterior: dict):
    layout = L.load_layout(S.LAYOUT_PATH)
    plan, _ = S.resolve(S.load_json(S.KIT_PATH), layout, S.PROPS_PATH, S.load_texture_set())
    chunks = HB.build(layout, exterior, plan)
    tris = []
    for mats in chunks.values():
        for md in mats.values():
            for f in md.faces:
                p = [md.verts[i] for i in f]
                tris += [(p[0], p[k], p[k + 1]) for k in range(1, len(p) - 1)]
    return np.array(tris, dtype=np.float64)


def _first(T, o, d) -> float | None:
    """Distance to the nearest triangle along the ray (either face), or None."""
    v0, e1, e2 = T[:, 0], T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
    pv = np.cross(d, e2)
    det = (e1 * pv).sum(1)
    ok = np.abs(det) > 1e-9
    inv = np.where(ok, 1.0 / np.where(ok, det, 1.0), 0.0)
    tv = o - v0
    u = (tv * pv).sum(1) * inv
    qv = np.cross(tv, e1)
    v = (qv * d).sum(1) * inv
    t = (e2 * qv).sum(1) * inv
    m = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0)
    return float(t[m].min()) if m.any() else None


def _hits(T, o, d) -> bool:
    return _first(T, o, d) is not None


class SilhouetteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(EXTERIOR, encoding="utf-8") as f:
            cls.ext = json.load(f)
        cls.T = _hlod_tris(cls.ext)

    def _through(self, eye, targets):
        o = np.array(eye, dtype=np.float64)
        out = []
        for t in targets:
            d = np.array(t, dtype=np.float64) - o
            d /= np.linalg.norm(d)
            if not _hits(self.T, o, d):
                out.append(t)
        return out

    def test_no_sky_through_the_castle_from_four_sides(self):
        """Rays from far off on each side into the castle's body (2 to 20 m up) all meet the
        silhouette: none passes through to the sky (the playtest's holes in the middle)."""
        views = {
            "south": ((10.0, 40.0, 360.0), [(x, y, 40.0) for x in range(-90, 100, 3) for y in range(2, 21, 3)]),
            "west": ((-330.0, 40.0, 20.0), [(-40.0, y, z) for z in range(-40, 80, 3) for y in range(2, 21, 3)]),
            "north": ((10.0, 40.0, -320.0), [(x, y, 10.0) for x in range(-90, 100, 3) for y in range(2, 21, 3)]),
            "east": ((340.0, 40.0, 20.0), [(30.0, y, z) for z in range(-40, 80, 3) for y in range(2, 21, 3)]),
        }
        for name, (eye, targets) in views.items():
            self.assertEqual([t for t in self._through(eye, targets) if not self._through_gate(eye, t)], [], name)

    @staticmethod
    def _through_gate(eye, t) -> bool:
        """Whether the ray runs through the courtyard's open gate (x 12..20, under 9.5 m at z 91):
        from afar it shows the sky through the Entrance Hall's open doors, a stand-in having no
        inside (an open item: a closed gate in the stand-in would show while the courtyard is loaded)."""
        if (eye[2] - 91.0) * (t[2] - 91.0) >= 0:
            return False
        k = (91.0 - eye[2]) / (t[2] - eye[2])
        x, y = eye[0] + (t[0] - eye[0]) * k, eye[1] + (t[1] - eye[1]) * k
        return 12.0 < x < 20.0 and y < 9.5

    def test_the_seventh_floor_corridor_stands_on_a_range(self):
        """Under the corridor's stand-in the far silhouette is solid down to the roofs below."""
        layout = L.load_layout(S.LAYOUT_PATH)
        s = layout.by_id("l7_corridor_east")
        for x in (s.x0 + 2.0, (s.x0 + s.x1) / 2, s.x1 - 2.0):
            for y in (30.0, 45.0, s.y - 1.0):
                # From the south, under the corridor: the ray stops at the range below it
                # (before, it ran on to the north ranges 40 m behind).
                t = _first(self.T, np.array((x, y, 200.0)), np.array((0.0, 0.0, -1.0)))
                self.assertIsNotNone(t, (x, y))
                self.assertGreater(200.0 - t, s.z0 - 1.0, (x, y))

    def test_the_courtyard_paving_is_in_its_stand_in(self):
        """From afar, through the gate, the courtyard's paving (not the sky under the castle)."""
        o = np.array((16.0, 20.0, 300.0))
        d = np.array((16.0, 1.0, 91.0)) - o
        d /= np.linalg.norm(d)
        t = _first(self.T, o, d)
        self.assertIsNotNone(t)
        self.assertGreater(float((o + d * t)[2]), 42.0)  # on the courtyard's floor

    def test_new_masses_do_not_overlap(self):
        boxes = [m for m in self.ext["masses"] if m["shape"] == "box" and "base" in m and m["chunk"] == "hlod_north"]
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                ox = min(a["max"][0], b["max"][0]) - max(a["min"][0], b["min"][0])
                oz = min(a["max"][1], b["max"][1]) - max(a["min"][1], b["min"][1])
                oy = min(a["top"], b["top"]) - max(a["base"], b["base"])
                self.assertFalse(ox > 1e-6 and oz > 1e-6 and oy > 1e-6, (a["id"], b["id"]))


class HouseEntranceTests(unittest.TestCase):
    def test_four_moving_entrances(self):
        lay = builder().layout
        for door, cell, style in HOUSE_ENTRANCES:
            self.assertIsNotNone(getattr(lay.by_id(door), "house", None), door)
            cb = build(cell)
            nodes = LN.house_entrance_nodes(cb).get(door)
            self.assertTrue(nodes, door)
            for name, anim in nodes.items():
                grp = R.P3D_ENTRANCE_PREFIX + door
                want = G.leaf_group(grp, anim.get("leaf")) if style == "double" else grp  # a double door: a node per leaf
                self.assertEqual(name.split(".")[-1], want)
                self.assertEqual((anim["kind"], anim["door"], anim["style"]), (G.ANIM_ENTRANCE, door, style), name)
                self.assertTrue(all(f.walkthrough for f in cb.meshes[name].src_faces), name)
                self.assertIsNotNone(cb.meshes[name].origin, name)
            rep = LN.Report(cell)
            LN.check_house_entrances_f1(cb, lay, rep)
            self.assertEqual(rep.errors, [], cell)
            self.assertLessEqual(cb.draws(), G.BUDGETS[cb.plan.budget].draws, cell)


class KnockerDoorPairTests(unittest.TestCase):
    """Knocker door opens as a pair: the knocker door is two leaves, a node each
    (with its knocker), each hinged at its outer edge."""

    def test_two_leaves_hinged_at_their_outer_edges(self):
        from castle_kit import pieces as P
        from castle_kit.pieces import tower

        cb = build("west_tower_stair")
        nodes = LN.house_entrance_nodes(cb)["cr_c_door"]
        grp = R.P3D_ENTRANCE_PREFIX + "cr_c_door"
        hero = next(u for u in cb.uses if u.piece == G.KNOCKER_DOOR)
        self.assertAlmostEqual(hero.yaw, 0.0)  # the piece's +x is world +x, its +z the landing
        self.assertEqual(sorted(nodes), sorted(G.node_name("west_tower_stair", m, G.leaf_group(grp, leaf))
                                               for m in ("castle_props", "castle_trim", "castle_wood")
                                               for leaf in G.ENTRANCE_LEAVES))
        self.assertEqual({a["leaf"] for a in nodes.values()}, {"l", "r"})
        self.assertTrue(all(a["style"] == "double" for a in nodes.values()))
        # Nothing of the door stays in a node of its own (no frame in this piece: the arch is the wall's).
        self.assertFalse([n for n in cb.meshes if n.endswith("." + grp)])
        w = tower.KNOCKER_DOOR_SIZE[0]
        x0 = hero.origin[0]
        leaf_pts = {leaf: [p for n, a in nodes.items() if a["leaf"] == leaf for f in cb.meshes[n].src_faces for p in f.pts]
                    for leaf in G.ENTRANCE_LEAVES}
        for name, anim in nodes.items():
            md = cb.meshes[name]
            xs = [p[0] for p in leaf_pts[anim["leaf"]]]  # the whole leaf (wood, straps, knocker)
            zs = [p[2] for p in leaf_pts[anim["leaf"]]]
            o = md.origin
            if anim["leaf"] == "l":  # the west leaf: all of it west of the meeting stile, its hinge the west jamb
                self.assertLessEqual(max(xs), x0 + 1e-6, name)
                self.assertAlmostEqual(o[0], x0 - w / 2, places=6, msg=name)
                self.assertAlmostEqual(o[0], min(xs), places=6, msg=name)
            else:
                self.assertGreaterEqual(min(xs), x0 - 1e-6, name)
                self.assertAlmostEqual(o[0], x0 + w / 2, places=6, msg=name)
                self.assertAlmostEqual(o[0], max(xs), places=6, msg=name)
            # On the back face (toward the common room, -z), at the floor.
            self.assertAlmostEqual(o[2], min(zs), places=6, msg=name)
            self.assertLess(o[2], hero.origin[2])
            self.assertAlmostEqual(o[1], hero.origin[1], places=6, msg=name)
        # Each leaf carries its own knocker (bronze on the props node), mirrored.
        props = {a["leaf"]: cb.meshes[n] for n, a in nodes.items() if ".castle_props." in n}
        span = {k: (min(p[0] for f in md.src_faces for p in f.pts) - x0, max(p[0] for f in md.src_faces for p in f.pts) - x0)
                for k, md in props.items()}
        self.assertAlmostEqual(span["l"][0], -span["r"][1], places=6)
        self.assertAlmostEqual(span["l"][1], -span["r"][0], places=6)
        self.assertGreater(span["r"][0], 0.05)  # clear of the meeting stile
        # The piece itself: every face on a leaf, the hinges in its meta.
        door = P.build(G.KNOCKER_DOOR)
        self.assertEqual({f.group for f in door.faces}, {G.LEAF_TAG.format("l"), G.LEAF_TAG.format("r")})
        back = -tower.KNOCKER_INSET - tower.KNOCKER_LEAF_THICK
        self.assertEqual(door.meta["hinges"], {"l": (-w / 2, 0.0, back), "r": (w / 2, 0.0, back)})
        self.assertLessEqual(door.triangles(), G.KNOCKER_DOOR_TRIS)

    def test_open_leaves_stay_inside_the_wall(self):
        """Turned DOUBLE_DOOR_TURN into the passage, no part of a leaf comes out through the
        common room's wall above its arch (the same 4 x 4.5 opening on the room face); a few
        degrees more and the meeting stiles' tops would."""
        import math

        cb = build("west_tower_stair")
        lay = builder().layout
        door = lay.by_id("cr_c_door")
        hero = next(u for u in cb.uses if u.piece == G.KNOCKER_DOOR)
        room_face = door.z0  # the passage runs from the landing (z -4) to the common room (z -6)
        self.assertLess(room_face, hero.origin[2])
        nodes = LN.house_entrance_nodes(cb)["cr_c_door"]

        def poke(turn: float) -> float:
            worst = 0.0
            for name, anim in nodes.items():
                md = cb.meshes[name]
                hx, hy, hz = md.origin
                sx = -1.0 if anim["leaf"] == "l" else 1.0
                for f in md.src_faces:
                    for x, y, z in f.pts:
                        dx, dz = x - hx, z - hz
                        # About +y by `turn`, the leaf's free edge toward -z (the common room).
                        a = turn * sx
                        rx = dx * math.cos(a) - dz * math.sin(a)
                        rz = dx * math.sin(a) + dz * math.cos(a)
                        px, pz = hx + rx, hz + rz
                        if pz < room_face - 1e-6:
                            off = min(abs(px - hero.origin[0]), 2.0)
                            worst = max(worst, (y - hy) - G.arch_height(4.0, 4.5, off))
            return worst

        # (the turn's sense: the west leaf's free edge goes to -z)
        self.assertLessEqual(poke(G.DOUBLE_DOOR_TURN), 1e-6)
        self.assertGreater(poke(G.DOUBLE_DOOR_TURN + math.radians(5)), 0.1)
        self.assertGreater(poke(math.pi / 2), 1.0)  # a quarter turn: tall fins in the common room

    def test_sub_groups(self):
        self.assertEqual(G.leaf_group("entrance_cr_c_door", "l"), "entrance_cr_c_door_leaf_l")
        self.assertIsNone(G.sub_group(None, G.LEAF_TAG.format("r")))  # the piece as furniture: the main mesh
        self.assertEqual(G.sub_group("l1", None), "l1")
        self.assertEqual(G.sub_group("l1", "candles_0"), "candles_0")
        self.assertTrue(G.GROUP_RE.match("entrance_cr_c_door_leaf_r"))
        self.assertIn("double", G.ENTRANCE_STYLES)
        self.assertEqual(G.HOUSE_ENTRANCE_STYLES[G.KNOCKER_DOOR], "double")

    def test_lint_refuses_a_leaf_hinged_in_the_middle(self):
        cb = build("west_tower_stair")
        nodes = LN.house_entrance_nodes(cb)["cr_c_door"]
        name = next(n for n, a in nodes.items() if a["leaf"] == "r" and ".castle_wood." in n)
        md = cb.meshes[name]
        saved = md.origin
        try:
            md.origin = (saved[0] - 2.0, saved[1], saved[2])  # at the meeting stile
            errs = LN.double_door_leaves(cb, "cr_c_door", nodes)
            self.assertTrue(any("origins" in e for e in errs), errs)
            for n, a in nodes.items():
                if a["leaf"] == "r":
                    cb.meshes[n].origin = md.origin
            errs = LN.double_door_leaves(cb, "cr_c_door", nodes)
            self.assertTrue(any("outer edge" in e for e in errs), errs)
        finally:
            for n, a in nodes.items():
                if a["leaf"] == "r":
                    cb.meshes[n].origin = saved
        self.assertEqual(LN.double_door_leaves(cb, "cr_c_door", nodes), [])


if __name__ == "__main__":
    unittest.main()
