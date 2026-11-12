"""Unit tests for the castle kit's pieces, rules, assembly and lint (castle
kit M1, the Pieces work; docs/art/castle-kit.md): the piece catalogue
(names, face tags, triangles, the hard rules each piece keeps by itself),
the Planner's wall openings, the M1.0 geometry of un-kitted cells,
byte-identical placements, the test bed's lint (intrusion, doors, overhead,
treads, coverage, z-fighting, degenerate faces, UVs, budgets, the
collider) and a synthetic cell with a flight, balustrades and outside
corners; castle kit M2: the plain shaft, the open-leaf rule, the vaults,
the traceried window, the hall's heroes, the glow colours, the Entrance
Hall built and linted, the railing-edge rule in the lint. Pure Python; run
through tests/run_tests.py."""

import hashlib
import json
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.dirname(HERE), os.path.join(os.path.dirname(HERE), "world")]

import kit  # noqa: E402
import layout_lib as L  # noqa: E402
from castle_kit import assemble as A  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import lint as LN  # noqa: E402
from castle_kit import pieces as P  # noqa: E402
from castle_kit import props_atlas as PA  # noqa: E402
from castle_kit import rules as R  # noqa: E402
from castle_kit import schema as S  # noqa: E402
from castle_kit.pieces import geom  # noqa: E402

LAYOUT_PATH = os.path.join(REPO, "data", "world", "layout.json")
PROPS_PATH = os.path.join(REPO, "data", "world", "props.json")
TEXTURES_PATH = os.path.join(REPO, "data", "art", "castle_textures.json")
FINGERPRINTS = os.path.join(HERE, "data", "m10_geometry.json")
TEST_BED = ("gf_corridor_west", "ff_corridor_north")

_BUILDER = None


def builder() -> A.Builder:
    global _BUILDER
    if _BUILDER is None:
        _BUILDER = A.Builder.from_files()
    return _BUILDER


def _h(obj) -> str:
    return hashlib.sha1(json.dumps(obj, separators=(",", ":")).encode()).hexdigest()[:16]


def _r(v):
    return round(v, 6) + 0.0


class PieceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.built = {name: P.build(name, **params) for name, params in P.CATALOGUE.items()}

    def test_names_tags_and_faces(self):
        textures = S.load_texture_set(TEXTURES_PATH)
        for name, p in self.built.items():
            self.assertTrue(G.PIECE_RE.match(name), name)
            # Vegetation look A: a tree or hedge piece is plantings of the foliage kit, no faces of its own.
            self.assertGreater(p.triangles() + len(p.meta.get("foliage", [])), 0, name)
            for f in p.faces + (p.lod1 or []):
                tag = G.parse_face_tag(f.tag)
                mat = {"castle_wall": "castle_wall_a", "castle_floor": "castle_floor_square",
                       "castle_portraits": "castle_portraits_a"}.get(tag.material, tag.material)  # (M4: the portrait family)
                self.assertIn(mat, textures["materials"], (name, f.tag))
                self.assertGreater(f.area(), 1e-6, (name, f.tag, f.pts[:2]))
                self.assertTrue(geom.is_convex(f.pts), (name, f.tag))
                if tag.mode == "trim":
                    sheet = textures["sheets"][textures["materials"][mat]["texture"]]
                    self.assertIn(tag.arg, sheet["strips"], (name, f.tag))
                    self.assertEqual(len(f.uv), len(f.pts))
                elif tag.mode == "fit":
                    sheet = textures["sheets"][textures["materials"][mat]["texture"]]
                    self.assertIn(tag.arg, sheet["tiles"], (name, f.tag))
                    self.assertTrue(all(-1e-9 <= a <= 1 + 1e-9 and -1e-9 <= b <= 1 + 1e-9 for a, b in f.uv), (name, f.tag))
                elif tag.mode == "kit":
                    tile, _, colour = tag.arg.partition(":")
                    self.assertIn(tile, kit.TILES)

    def test_triangle_budgets(self):
        """The pieces' triangle ranges (LOD0), with room for the clustered
        shafts and the richer frames chosen for the toon outlines."""
        ranges = {
            "kit_stone_arch_": (300, 900), "kit_window_lancet_": (300, 1000), "kit_niche_": (150, 500),
            "kit_door_blind_": (200, 500), "kit_door_oak_": (100, 450), "kit_threshold_": (4, 12),
            "kit_stone_corbel_": (20, 90), "kit_lantern_wall": (100, 250),
            "kit_lantern_hanging": (120, 300), "kit_banner_": (10, 80), "kit_newel_": (100, 300),
            # Castle kit M2 (docs/art/castle-kit.md).
            "kit_window_tracery_": (600, 2500), "kit_vault_lierne_bay_": (1000, 3000), "kit_vault_fan_": (1500, 6000),
            "kit_armour_suit": (300, 1200), "kit_owl_perch": (60, 300),
        }
        for name, p in self.built.items():
            for prefix, (lo, hi) in ranges.items():
                if name.startswith(prefix) and not name.startswith(G.CUP_BANNER):  # Many banners a piece
                    self.assertTrue(lo <= p.triangles() <= hi, f"{name}: {p.triangles()} not in {lo}..{hi}")
            if name.startswith("kit_stone_shaft_"):
                variant = "plain" if name.endswith("_plain") else "clustered"
                self.assertTrue(80 <= p.triangles() <= G.SHAFT_TRIS[variant], f"{name}: {p.triangles()}")
        bal = self.built["kit_balustrade_gothic"]
        length = P.CATALOGUE["kit_balustrade_gothic"]["length"]
        self.assertLessEqual(bal.triangles() / length, 80)
        self.assertLessEqual(sum(len(f.pts) - 2 for f in bal.lod1), 12 * length + 24)

    def test_relief_below_head_height(self):
        """Wall pieces stand at most 0.25 m proud below 2.4 m (the intrusion
        rule), at least 0.1 m somewhere (outlines catch them)."""
        for name in ("kit_stone_plinth", "kit_stone_string", "kit_stone_shaft_8.4", "kit_stone_quoin", "kit_niche_2x3.5",
                     "kit_door_blind_3x4.5", "kit_stone_arch_5x6", "kit_window_lancet_1.6x5", "kit_stone_shaft_8.4_plain",
                     "kit_stone_shaft_14_plain", "kit_door_oak_8x10_open", "kit_door_oak_6x9_open"):
            p = self.built[name]
            low = [pt[2] for f in p.faces for pt in f.pts if pt[1] < G.INTRUSION_HEIGHT - 1e-6]
            if low:
                self.assertLessEqual(max(low), G.INTRUSION_MAX + 1e-9, name)
            self.assertGreaterEqual(max(pt[2] for f in p.faces for pt in f.pts), G.MIN_RELIEF - 1e-9, name)

    def test_profiles_match_the_grid(self):
        for name, m in (("kit_stone_plinth", G.PLINTH), ("kit_stone_string", G.STRING)):
            p = self.built[name]
            self.assertAlmostEqual(max(pt[2] for f in p.faces for pt in f.pts), m.proud)
            self.assertAlmostEqual(max(pt[1] for f in p.faces for pt in f.pts), m.height)
        c = self.built["kit_stone_cornice"]
        self.assertAlmostEqual(min(pt[1] for f in c.faces for pt in f.pts), -G.CORNICE.height)
        self.assertAlmostEqual(max(pt[2] for f in c.faces for pt in f.pts), G.CORNICE.proud)
        sh = self.built["kit_stone_shaft_8.4"]
        body = [pt for f in sh.faces if f.tag == "castle_trim|trim:shaft" for pt in f.pts]
        xs = [pt[0] for pt in body]
        self.assertAlmostEqual(max(xs) - min(xs), G.SHAFT.height)
        self.assertAlmostEqual(max(pt[2] for pt in body), G.SHAFT.proud)
        self.assertLessEqual(max(pt[1] for f in sh.faces for pt in f.pts), 8.4 - G.CORNICE.height + 1e-9)

    def test_arches_follow_the_door(self):
        for w, h in G.ARCH_SIZES:
            p = P.build(G.arch_piece(w, h), reveal=0.5, depth=2.0)
            spring = G.arch_springing(w, h)
            # Nothing of the arch inside the opening below its intrados.
            for f in p.faces:
                for x, y, z in f.pts:
                    if abs(x) < w / 2 - 1e-6 and z < 1e-6:
                        self.assertGreaterEqual(y + 1e-6, G.arch_height(w, h, x), (w, h, x, y))
            self.assertLess(p.meta["spring"], h)
            self.assertAlmostEqual(p.meta["spring"], spring)
            self.assertGreaterEqual(G.arch_clear_height(w, h), G.DOOR_CLEAR_HEIGHT)
            # The reveal: nothing deeper than the given reveal.
            self.assertGreaterEqual(min(pt[2] for f in p.faces for pt in f.pts), -0.5 - 1e-9)

    def test_lanterns_hang_at_most_the_schema_drop(self):
        self.assertLessEqual(self.built["kit_lantern_wall"].meta["drop"], S.LANTERN_DROP + 1e-9)

    def test_flight_treads_on_the_ramp(self):
        for rise, length in ((1.7, 3.0), (9.0, 15.6), (0.5, 1.0)):
            p = P.build("kit_stair_flight", width=4.0, length=length, rise=rise, solid=True)
            for zm, top in p.meta["treads"]:
                self.assertLessEqual(abs(top - rise * zm / length), G.TREAD_RAMP_TOLERANCE)
            self.assertLessEqual(abs(p.meta["riser"] - G.RISER), 0.02)

    def test_open_door_leaves_keep_a_clear_metre(self):
        p = self.built["kit_door_oak_5x6_open"]
        left, right = p.meta["clear"]
        self.assertGreaterEqual(5.0 - left - right, G.DOOR_CLEAR_WIDTH)

    def test_open_leaf_rule(self):
        """Castle kit M2 (docs/art/castle-kit.md): open leaves, each half
        the span, never across the passage: on the wall face beyond the
        quoins (`face`) or flat against the jambs inside the reveal
        (`reveal`), at most LEAF_PROUD off the face they lie on."""
        for w, h, reveal in ((8.0, 10.0, 2.0), (6.0, 9.0, 0.5), (5.0, 6.0, 0.5)):
            p = P.build(G.piece_name("door", "oak", w, h, "open"), depth=2.0, mode="face", reveal=reveal)
            half, leaf = w / 2, G.leaf_width(w)
            spring = G.arch_springing(w, h)
            self.assertEqual((p.meta["mode"], p.meta["leaf"]), ("face", leaf))
            for f in p.faces:
                for x, y, z in f.pts:
                    if z > 0:  # on the wall face: beside the opening, beyond the quoins
                        self.assertTrue(half + G.ARCH_SURROUND - 1e-6 <= abs(x) <= half + G.ARCH_SURROUND + leaf + 1e-6, (w, x))
                        self.assertLessEqual(z, G.LEAF_PROUD + 1e-9)
                        self.assertLessEqual(y, spring + 1e-6)
                    else:  # the tympanum, inside the reveal it lines, above the springing
                        self.assertGreaterEqual(y, spring - 1e-6)
                        self.assertGreaterEqual(z, -reveal - 1e-6)
            self.assertEqual(p.meta["clear"], (0.0, 0.0))
        p = P.build("kit_door_oak_3x4.5_open", depth=2.0, mode="reveal", reveal=2.0)
        for f in p.faces:
            for x, y, z in f.pts:
                self.assertGreaterEqual(z, -(G.leaf_width(3.0) + G.LEAF_REVEAL_CLEAR) - 1e-6)
                self.assertLessEqual(z, 1e-6)
                if y < G.arch_springing(3.0, 4.5) - 1e-6:
                    self.assertGreaterEqual(abs(x), 1.5 - G.LEAF_PROUD - 1e-6)
        self.assertGreaterEqual(3.0 - sum(p.meta["clear"]), G.DOOR_CLEAR_WIDTH)
        self.assertLessEqual(p.meta["proud"], G.LEAF_PROUD)
        with self.assertRaises(ValueError):
            P.build("kit_door_oak_8x10_open", depth=2.0, mode="reveal", reveal=2.0)  # a 4 m leaf needs 4.1 m

    def test_plain_shaft(self):
        """Castle kit M2: the repeated shaft is 0.7 wide, 0.25 proud, at most
        SHAFT_TRIS["plain"] triangles, its top under the cornice."""
        for h in (8.4, 14.0):
            p = P.build(G.shaft_piece(h))
            self.assertLessEqual(p.triangles(), G.SHAFT_TRIS["plain"])
            body = [pt for f in p.faces if f.tag == "castle_trim|trim:shaft" for pt in f.pts]
            self.assertAlmostEqual(max(pt[0] for pt in body) - min(pt[0] for pt in body), G.SHAFT.height)
            self.assertAlmostEqual(max(pt[2] for pt in body), G.SHAFT.proud)
            self.assertAlmostEqual(max(pt[1] for f in p.faces for pt in f.pts), h - G.CORNICE.height)
        self.assertEqual(P.build("kit_stone_shaft_14").triangles(), P.build("kit_stone_shaft_8.4").triangles())  # clustered

    def test_vaults(self):
        """The lierne bay and the fan: below their apex (ribs and bosses buried
        at most 0.16 m into the web, the fan's curtains 0.3 m), the gap left open, the transverse arch
        shared by neighbouring bays, coarse collider shells."""
        from castle_kit.pieces import vaults

        bay = P.build("kit_vault_lierne_bay_32x10", rise=6.0)
        gap = P.build("kit_vault_lierne_bay_32x10", rise=6.0, gap=12.0)
        fan = P.build("kit_vault_fan_12x10", rise=6.0)
        for p, above in ((bay, 0.16), (gap, 0.16), (fan, vaults.CURTAIN_UP)):
            ys = [pt[1] for f in p.faces for pt in f.pts]
            self.assertLessEqual(max(ys), 6.0 + above + 1e-9)  # within the 0.6 m slab over the layout ceiling
            self.assertTrue(p.meta["collider"])
        for f in gap.faces:
            if f.tag == vaults.WEB:
                self.assertTrue(all(abs(x) >= 6.0 - 1e-6 for x, _, _ in f.pts) or not all(abs(x) < 6.0 - 1e-6 for x, _, _ in f.pts))
        self.assertFalse([f for f in gap.faces if f.tag == vaults.WEB and all(abs(x) < 5.99 for x, _, _ in f.pts)])
        S_ = vaults.LierneSurface(32, 10, 6)
        for x in (-15.0, -8.0, 0.0, 3.0, 12.0):
            self.assertAlmostEqual(S_.height(x, -5.0), S_.transverse(x))
            self.assertAlmostEqual(S_.height(x, 5.0), S_.transverse(x))
        for z in (-4.0, 0.0, 2.5):
            self.assertAlmostEqual(S_.height(16.0, z), S_.wall(z))
        self.assertAlmostEqual(S_.height(0.0, 0.0), 6.0)
        F = vaults.FanSurface(12, 10, 6)
        self.assertAlmostEqual(F.height(-6.0, -5.0), 0.0)  # springers on the wall
        self.assertAlmostEqual(F.height(6.0, 5.0), vaults.DEFAULT_PENDANT)  # pendants
        self.assertAlmostEqual(F.height(0.0, 0.0), 6.0)

    def test_tracery_window(self):
        p = P.build("kit_window_tracery_8x8", depth=0.8)
        self.assertEqual(p.meta["lights"], 4)
        glass = [f for f in p.faces if f.tag.startswith("castle_glass")]
        self.assertEqual(len({f.tag for f in glass}), 2)  # lights and the head
        closed = P.build("kit_window_tracery_8x8", depth=0.8, outer=False)
        self.assertLess(closed.triangles(), p.triangles())
        self.assertFalse([f for f in closed.faces if all(abs(pt[2] + 0.8) < 1e-9 for pt in f.pts)])

    def test_hall_heroes(self):
        armour = P.build("kit_armour_suit")
        xs = [pt[0] for f in armour.faces for pt in f.pts]
        zs = [pt[2] for f in armour.faces for pt in f.pts]
        ys = [pt[1] for f in armour.faces for pt in f.pts]
        # In a 2 x 3.5 niche 0.7 deep with its feet 0.35 m behind the face.
        self.assertLess(max(abs(x) for x in xs), 1.0 - 0.3)
        self.assertLess(max(zs), 0.35 - 0.05)
        self.assertGreater(min(zs), -0.35 + 0.1)
        self.assertLess(max(ys), G.arch_height(2.0, 3.5, 0.4))
        counter = P.build("kit_owl_counter", width=12.0)
        cx = [pt[0] for f in counter.faces for pt in f.pts]
        self.assertAlmostEqual(max(cx) - min(cx), 12.0 + 0.14)
        self.assertTrue(counter.walkthrough)
        self.assertEqual(P.build("kit_owl_counter").triangles(), kit.km.build("owl_exchange_counter").mesh.triangle_count())
        court = counter.triangles() + 2 * P.build("kit_owl_perch").triangles() + 2 * P.build("kit_lantern_hanging",
                                                                                                drop=13.0).triangles()
        self.assertLessEqual(court, 6000)
        for name in ("kit_armour_suit", "kit_owl_counter", "kit_owl_perch"):
            self.assertTrue(all(f.tag.startswith("castle_props|kit:") for f in P.build(name).faces), name)

    def test_glow_colours(self):
        """Castle kit M2, the glow rule: lanterns and lamps glow GLOW_LANTERN,
        candles GLOW_CANDLE, on slots the shared atlas holds."""
        from castle_kit.pieces import fixtures

        # Castle kit M3: the fixtures name their colours (castle_textures.json palette).
        self.assertEqual(fixtures.GLOW, "castle_props|kit:glow:@glow_lantern")
        self.assertEqual(fixtures.CANDLE, "castle_props|kit:glow:@glow_candle")
        _, atlas = PA.build(PROPS_PATH, TEXTURES_PATH)
        for tile, colour in fixtures.SLOTS:
            atlas.index(tile, colour)
        self.assertEqual(atlas.colour("@glow_lantern"), G.GLOW_LANTERN)
        self.assertEqual(atlas.colour("@glow_candle"), G.GLOW_CANDLE)
        import palette

        self.assertEqual(palette.GLOW["candle"], G.GLOW_CANDLE)
        for name in ("kit_lantern_wall", "kit_lantern_hanging", "kit_newel_lamp"):
            glows = {f.tag for f in P.build(name).faces if ":glow:" in f.tag}
            self.assertEqual(glows, {fixtures.GLOW}, name)

    def test_parse_names(self):
        self.assertEqual(P.parse("kit_stone_arch_5x6"), ("kit_stone_arch", 5.0, 6.0, None))
        self.assertEqual(P.parse("kit_door_oak_8x10_open"), ("kit_door_oak", 8.0, 10.0, "open"))
        self.assertEqual(P.parse("kit_stone_shaft_8.4"), ("kit_stone_shaft", 8.4, None, None))
        self.assertEqual(P.parse("kit_lantern_wall"), ("kit_lantern_wall", None, None, None))
        with self.assertRaises(KeyError):
            P.build("kit_stone_nonesuch")


class GeomTests(unittest.TestCase):
    def test_pointed_arc_runs_jamb_apex_jamb(self):
        arc = geom.pointed_arc(5.0, 3.25, 8)
        self.assertEqual(len(arc), 17)
        self.assertAlmostEqual(arc[0][0], -2.5)
        self.assertAlmostEqual(arc[0][1], 3.25)
        self.assertAlmostEqual(arc[8][0], 0.0)
        self.assertAlmostEqual(arc[8][1], 6.0)
        self.assertAlmostEqual(arc[-1][0], 2.5)
        xs = [p[0] for p in arc]
        self.assertEqual(xs, sorted(xs))
        for x, y in arc:
            self.assertAlmostEqual(y, G.arch_height(5.0, 6.0, x), places=6)

    def test_concave_faces_are_ear_clipped(self):
        out = []
        geom.add_face(out, [(0, 0, 0), (2, 0, 0), (2, 2, 0), (1, 1, 0), (0, 2, 0)], "castle_trim|fit:plaque",
                      [(0, 0), (1, 0), (1, 1), (0.5, 0.5), (0, 1)], (0, 0, 1))
        self.assertEqual(len(out), 3)
        self.assertAlmostEqual(sum(f.area() for f in out), 3.0)
        self.assertTrue(all(f.normal()[2] > 0.99 for f in out))

    def test_sweep_mitres_meet_on_the_diagonal(self):
        a, b = [], []
        geom.sweep(a, [(0.0, 0.0), (0.2, 0.0), (0.2, 0.5), (0.0, 0.5)], 0.0, 3.0, "castle_trim|trim:plinth", 0.6,
                   ends=("cap", "miter"))
        far = [pt for f in a for pt in f.pts if pt[0] > 2.5]
        for x, _, z in far:
            self.assertAlmostEqual(x, 3.0 - z)
        _ = b


class WallOpeningTests(unittest.TestCase):
    def _layout(self):
        return L.parse_layout({"version": 1, "spawns": [{"id": "s", "pos": [1, 0, 1], "yaw": 0}], "surfaces": [
            {"id": "a", "kind": "corridor", "name": "a", "level": 0, "cell": "c", "min": [0, 0], "max": [20, 10],
             "y": 0, "ceiling": 8.4}]})

    def test_holes_cut_the_face_and_through_holes_the_shell(self):
        lay = self._layout()
        plain = L.plan_layout(lay)
        holes = [L.WallHole("a", "z", 0.0, 1.0, 4.0, 5.6, 2.4, 7.4, True),
                 L.WallHole("a", "z", 0.0, 1.0, 10.0, 12.0, 0.6, 4.1, False)]
        cut = L.plan_layout(lay, holes=holes)

        def area(plan, mat, pred):
            return sum(geom.norm(geom.newell(p)) / 2 for p in plan.polys[("c", mat)] if pred(p))

        on_face = lambda p: all(abs(q[2]) < 1e-9 for q in p)  # noqa: E731
        outer = lambda p: all(abs(q[2] + 0.8) < 1e-9 for q in p)  # noqa: E731
        self.assertAlmostEqual(area(plain, "wall", on_face) - area(cut, "wall", on_face), 1.6 * 5 + 2 * 3.5)
        self.assertAlmostEqual(area(plain, "wall_exterior", outer) - area(cut, "wall_exterior", outer), 1.6 * 5)
        self.assertEqual(L.degenerate_polygons(cut), [])
        self.assertEqual(L.find_z_fighting(cut), [])
        # Everything else is the plain plan.
        for key in plain.polys:
            if key[1] not in ("wall", "wall_exterior"):
                self.assertEqual(plain.polys[key], cut.polys[key])

    def test_no_holes_is_the_m10_plan(self):
        lay = self._layout()
        self.assertEqual(L.plan_layout(lay).polys, L.plan_layout(lay, holes=()).polys)

    def test_holes_need_flat_walls(self):
        lay = L.parse_layout({"version": 1, "spawns": [{"id": "s", "pos": [1, 0, 1], "yaw": 0}], "surfaces": [
            {"id": "st", "kind": "stair", "name": "st", "level": 0, "cell": "c", "min": [0, 0], "max": [6, 3], "y": 0,
             "y_top": 3, "rise": "+x", "ceiling": 8.4}]})
        with self.assertRaises(L.LayoutError):
            L.plan_layout(lay, holes=[L.WallHole("st", "z", 0.0, 1.0, 1.0, 2.0, 1.0, 2.0, False)])


M5C_SHELL_CUT = ("courtyard",)  # Castle M5c (Facades): plan1 cut through its wall mass with the hall's


class M10GeometryTests(unittest.TestCase):
    """Un-kitted cells export exactly the M1.0 build: the Planner's polygons
    (also with the kitted cells' holes in the plan) and the props kit's
    geometry and UVs on the shared atlas (fingerprints recorded at commit
    f9a9395, tests/data/m10_geometry.json; the exported glbs were compared
    too)."""

    @classmethod
    def setUpClass(cls):
        with open(FINGERPRINTS, encoding="utf-8") as f:
            cls.fp = json.load(f)
        cls.b = builder()
        cls.kitted = set(cls.b.kit_plan.cells)

    def _plan_fp(self, plan):
        out = {}
        for (c, m), ps in sorted(plan.polys.items()):
            out.setdefault(c, {})[m] = _h([[[_r(x) for x in p] for p in poly] for poly in ps])
        return out

    def test_planner_polygons(self):
        for which, plan in (("plan0", self.b.plan0), ("plan1", self.b.plan1)):
            got = self._plan_fp(plan)
            for cell, mats in self.fp["plan"].items():
                if cell in self.kitted:
                    continue
                if which == "plan1" and cell in M5C_SHELL_CUT:
                    continue  # Castle M5c (Facades): the outer-shell cut opens its wall (plan0 unchanged)
                self.assertEqual(got.get(cell), mats, f"{which}: {cell}")

    def test_props_kit_on_the_shared_atlas(self):
        for cell, ck in self.b.cell_kits.items():
            if cell in self.kitted:
                continue
            got = _h({"v": [[_r(x) for x in v] for v in ck.mesh.verts], "f": [list(f.idx) for f in ck.mesh.faces],
                      "uv": [[[_r(a) for a in uv] for uv in fu] for fu in ck.uvs],
                      "flats": {k: [[[_r(x) for x in p] for p in poly] for poly in v] for k, v in sorted(ck.flats.items())}})
            self.assertEqual(got, self.fp["kit"][cell], cell)

    def test_collider_is_the_m10_shell(self):
        for cell in TEST_BED:
            cb = self.b.build(cell)
            want = [p for (c, m), ps in self.b.plan0.polys.items() if c == cell
                    and (m in A.COLLIDER_MATERIALS or m.startswith("floor_")) for p in ps]
            heads = A.arch_head_closers(cb.plan)
            # Two quads per arch (the face and the reveal's end), above the springing line.
            self.assertEqual(len(heads), 2 * sum(1 for o in cb.plan.openings if o.kind == "arch"))
            self.assertEqual(sorted(map(str, cb.collider)), sorted(map(str, [list(p) for p in want] + heads)))
            self.assertLessEqual(cb.collider_triangles(), G.COLLIDER_TRIS)


class TestBedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = builder()
        cls.cells = {c: cls.b.build(c) for c in TEST_BED}
        cls.reports = {c: LN.lint_cell(cb, cls.b) for c, cb in cls.cells.items()}

    def test_lint_is_clean(self):
        for cell, rep in self.reports.items():
            self.assertEqual(rep.errors, [], cell)

    def test_intrusion_and_clearances(self):
        for cell, rep in self.reports.items():
            self.assertLessEqual(rep.stats["intrusion_max"], G.INTRUSION_MAX + 1e-9, cell)
            self.assertGreaterEqual(rep.stats["overhead_min"], G.OVERHEAD_CLEARANCE, cell)
            # Castle M5a N7 adds the Long Gallery arch to the first-floor corridor, and the
            # Trophy Cabinet Stair's door (its passage panel covers it).
            self.assertEqual(rep.stats["doors_checked"], 4 if cell == "ff_corridor_north" else 2, cell)
            self.assertEqual(rep.stats["coverage_missing"], 0, cell)
            self.assertEqual(rep.stats["zfight"], 0, cell)
            self.assertEqual(rep.stats["degenerate"], 0, cell)
            self.assertEqual(rep.stats["uv_bad"], 0, cell)

    def test_budgets_and_materials(self):
        want = {
            "gf_corridor_west": ["castle_ceiling", "castle_floor_square", "castle_glass", "castle_props", "castle_trim",
                                 "castle_wall_a"],
            "ff_corridor_north": ["castle_ceiling", "castle_floor_square", "castle_glass", "castle_props", "castle_props",
                                  "castle_trim", "castle_wall_a", "castle_wood"],  # + the passage entrance's node
        }
        for cell, cb in self.cells.items():
            self.assertEqual(sorted(m.material for m in cb.meshes.values()), want[cell])
            self.assertEqual(cb.library(), sorted(set(want[cell])))
            self.assertLessEqual(cb.draws(), G.BUDGETS["corridor"].draws)
            self.assertLessEqual(cb.triangles(), G.BUDGETS["corridor"].tris[1])
            # (M2's plain shafts took gf_corridor_west a little under the 10 k target: a warning, not an error.)
            self.assertGreaterEqual(cb.triangles(), 0.8 * G.BUDGETS["corridor"].tris[0])
            for u in cb.uses:
                if u.role == "shaft":
                    self.assertEqual(u.piece, "kit_stone_shaft_8.4_plain")
            self.assertEqual(cb.lod_meshes, {})  # the test bed needs no LOD
            for name, m in cb.meshes.items():
                if name.startswith(f"{cell}.castle_props.entrance_"):  # The passage entrance's moving node
                    continue
                self.assertEqual(name, f"{cell}.{m.material}")
                self.assertTrue(m.material.startswith("castle_"), name)
                self.assertEqual(len(m.uvs), len(m.faces))

    def test_placements_follow_castle_kit_json(self):
        counts = {c: {} for c in TEST_BED}
        for cell, cb in self.cells.items():
            for u in cb.uses:
                counts[cell][u.role] = counts[cell].get(u.role, 0) + 1
        self.assertEqual(counts["gf_corridor_west"]["shaft"], 6)
        self.assertEqual(counts["gf_corridor_west"]["lantern"], 2)
        self.assertEqual(counts["gf_corridor_west"]["arch"], 2)
        self.assertEqual(counts["gf_corridor_west"]["window"], 6)
        self.assertEqual(counts["gf_corridor_west"]["niche"], 2)
        self.assertNotIn("threshold", counts["gf_corridor_west"])
        # Castle M5a N7: the east end wall's two windows gave way to the Long Gallery arch.
        self.assertEqual(counts["ff_corridor_north"]["shaft"], 17)  # The north wall split at the passage
        self.assertEqual(counts["ff_corridor_north"]["lantern"], 8)  # The split north wall
        self.assertEqual(counts["ff_corridor_north"]["window"], 10)  # Bay 4 of the north wall
        self.assertEqual(counts["ff_corridor_north"]["niche"], 7)  # Bay 7's niche is the History door
        self.assertEqual(counts["ff_corridor_north"]["blind_door"], 3)
        self.assertEqual(counts["ff_corridor_north"]["threshold"], 1)
        pieces = {u.piece for u in self.cells["ff_corridor_north"].uses}
        self.assertIn("kit_stone_arch_8x7", pieces)
        self.assertIn("kit_threshold_8", pieces)
        # The Marble Arch's reveal covers its passage less the Entrance Hall's
        # 0.5 m since the hall is built too (castle kit M2); the others reach 0.5 m in.
        arches = {u.id: u.kwargs() for cb in self.cells.values() for u in cb.uses if u.role == "arch"}
        for uid, kw in arches.items():
            self.assertAlmostEqual(kw["reveal"], 1.5 if "ff_marble_arch" in uid else 0.5, msg=uid)

    def test_nothing_greybox_and_replaced_props_gone(self):
        ff = self.cells["ff_corridor_north"]
        self.assertTrue(all(f.material.startswith("castle_") for f in ff.faces))
        # The classroom door boxes (x 5..7 and 28.12..30.12, z -14..-13.85) are not drawn.
        for f in ff.faces:
            if f.src == "base":
                self.assertFalse(all(abs(p[2] + 13.85) < 1e-6 for p in f.pts))
        # The tells' flat faces are on the shared atlas.
        tells = [f for cb in self.cells.values() for f in cb.faces if f.src.startswith("props:tell_")]
        self.assertTrue(tells)
        self.assertTrue(all(f.material == "castle_props" and f.mode == "kit" for f in tells))

    def test_sweeps_break_at_the_loose_stone(self):
        """The plinth stops either side of the tell at x 38.5 on the north wall."""
        ff = self.cells["ff_corridor_north"]
        for f in ff.faces:
            if f.src.startswith("plinth:ff_corridor_north:n0"):
                for x, y, z in f.pts:
                    self.assertFalse(38.5 - G.TELL_HALF_WIDTH < x < 38.5 + G.TELL_HALF_WIDTH, (f.src, x))

    def test_world_uvs_keep_the_courses(self):
        """Wall faces: v = y / 4.8 everywhere (a course line every 0.6 m from
        y = 0, across corners, openings and cells)."""
        for cb in self.cells.values():
            for f in cb.faces:
                if f.material == "castle_wall_a" and f.mode == "world" and abs(f.normal()[1]) < 0.5:
                    for p, (u, v) in zip(f.pts, f.uv):
                        self.assertAlmostEqual(v, p[1] / G.STONE_TILE_M, places=9)

    def test_byte_identical(self):
        b2 = A.Builder.from_files()
        for cell, cb in self.cells.items():
            again = b2.build(cell)
            self.assertEqual(R.to_json(cb.uses), R.to_json(again.uses), cell)
            self.assertEqual(cb.digest(), again.digest(), cell)

    def test_camera_max(self):
        plan = self.b.kit_plan
        self.assertEqual(plan.camera_max("entrance_hall"), 24.0)
        self.assertEqual(plan.camera_max("common_room_house_b"), 20.0)
        self.assertEqual(plan.camera_max("gf_corridor_west"), 16.0)


class EntranceHallTests(unittest.TestCase):
    """Castle kit M2: the Entrance Hall built (docs/art/castle-kit.md)."""

    @classmethod
    def setUpClass(cls):
        cls.b = builder()
        cls.cb = cls.b.build("entrance_hall")
        cls.rep = LN.lint_cell(cls.cb, cls.b)
        cls.roles = {}
        for u in cls.cb.uses:
            cls.roles.setdefault(u.role, []).append(u)

    def test_lint_is_clean(self):
        self.assertEqual(self.rep.errors, [])
        st = self.rep.stats
        self.assertLessEqual(st["intrusion_max"], G.INTRUSION_MAX + 1e-9)
        self.assertGreaterEqual(st["overhead_min"], G.OVERHEAD_CLEARANCE)
        self.assertEqual(st["doors_checked"], 6)  # castle M5a N1 adds eh_north_door, castle M5c N5 eh_owl_gate
        self.assertLessEqual(st["tread_error_max"], G.TREAD_RAMP_TOLERANCE)
        for k in ("coverage_missing", "zfight", "degenerate", "uv_bad", "window_depth_bad", "off_wall"):
            self.assertEqual(st[k], 0, k)

    def test_budget_and_materials(self):
        cb = self.cb
        b = G.BUDGETS["entrance_hall"]
        self.assertLessEqual(cb.draws(), G.draw_cap("entrance_hall", "entrance_hall"))  # castle M5c: its facade
        self.assertLessEqual(cb.triangles(), b.tris[1])
        self.assertEqual(cb.library(), ["castle_ceiling", "castle_floor_long", "castle_glass", "castle_props",
                                        "castle_roof", "castle_trim", "castle_trim@ext", "castle_wall_b",
                                        "castle_wall_b@ext", "castle_wood"])  # (castle M5c: the facade's clones, roof)
        self.assertEqual(sorted(cb.lod_meshes), ["entrance_hall.castle_roof_lod1", "entrance_hall.castle_trim_lod1",
                                                 "entrance_hall.castle_wall_b@ext_lod1"])  # the balustrades', the facade's
        self.assertLessEqual(cb.collider_triangles(), G.COLLIDER_TRIS)
        self.assertTrue(all(f.material.startswith("castle_") for f in cb.faces))

    def test_collider_has_the_vault_shell(self):
        """camera.ts keeps 0.8 m off ceiling faces and the vault hangs up to
        6 m under the layout ceiling: the collider carries a coarse shell."""
        cb = self.cb
        plain = [p for (c, m), ps in self.b.plan0.polys.items() if c == "entrance_hall"
                 and (m in A.COLLIDER_MATERIALS or m.startswith("floor_")) for p in ps]
        extra = len(cb.collider) - len(plain) - len(A.arch_head_closers(cb.plan))
        self.assertGreater(extra, 100)
        shell = [p for p in cb.collider if all(14.0 - 1e-6 <= q[1] <= 20.5 for q in p) and min(q[1] for q in p) < 19.5]
        self.assertTrue(shell)

    def test_collider_has_the_counter_shell(self):
        """Review fix: the camera ended inside the walk-through Owl Exchange
        counter; its cabinet front (x 0.69, to its cornice at 3.7 m) and its
        desk front (x 2.59, 1.08 m) are collider faces along the counter."""
        fronts = {}
        for p in self.cb.collider:
            xs = {round(q[0], 3) for q in p}
            if len(xs) == 1 and xs & {0.69, 2.59}:
                zs = [q[2] for q in p]
                fronts[xs.pop()] = (min(zs), max(zs), max(q[1] for q in p))
        self.assertIn(0.69, fronts)
        self.assertIn(2.59, fronts)
        z0, z1, top = fronts[0.69]
        self.assertLessEqual(z0, 24.0)
        self.assertGreaterEqual(z1, 36.0)
        self.assertAlmostEqual(top, 3.7)
        self.assertAlmostEqual(fronts[2.59][2], 1.08)

    def test_vault_heroes_replace_the_flat_ceiling(self):
        cb = self.cb
        self.assertNotIn("rib", self.roles)  # no flat-ceiling ribs under the vault
        ceiling = [f for f in cb.faces if f.src == "base" and f.material == "castle_ceiling" and min(p[1] for p in f.pts) > 13.9]
        self.assertEqual(ceiling, [])
        for u in self.roles["cornice"]:
            self.assertAlmostEqual(u.origin[1], 14.0)  # the springing line
        self.assertEqual(len(self.roles["corbel"]), 5)  # vault lines with no shaft: west 10, 30; east 20; the fan's two springers
        self.assertEqual(sum(1 for u in self.roles["hero"] if u.piece.startswith("kit_vault_")), 5)

    def test_staircase(self):
        flight = self.roles["flight"]
        self.assertEqual(len(flight), 1)
        kw = flight[0].kwargs()
        self.assertEqual(kw["strip"], "nosing_marble")
        piece = self.cb.pieces[flight[0].id]
        self.assertEqual(piece.meta["steps"], 53)
        self.assertAlmostEqual(piece.meta["going"], G.TREAD_MARBLE, delta=0.01)
        on_stair = [u for u in self.roles["balustrade"] if u.run == "eh_marble_staircase"]
        self.assertEqual(len(on_stair), 4)  # both sides, split at the half-way lamp newels
        heroes = [u for u in self.roles["hero"] if u.piece == "kit_newel_lamp"]
        self.assertEqual(len(heroes), 4)
        for h in heroes:
            for n in self.roles["newel"]:
                self.assertGreater(math.hypot(n.origin[0] - h.origin[0], n.origin[2] - h.origin[2]), 0.35, (h.id, n.id))
        # Review fix: the treads are marble (the nosing strip's veined band), not the hall's flagstones.
        tops = [f for f in piece.faces if f.normal()[1] > 0.99]
        self.assertEqual(len(tops), 53 * 4)  # 8 m wide, split every 2.4 m (strips cannot wrap)
        for f in tops:
            self.assertEqual(f.tag, "castle_trim|trim:nosing_marble")
            self.assertTrue(all(0.0 < v < 0.2 for _, v in f.uv), f.uv)  # the riser's marble band, not the nose
        # Review fix: lamp standards: an iron post with a lantern whose foot stays
        # at least the overhead clearance above the stair.
        for h in heroes:
            kw = h.kwargs()
            lamp = self.cb.pieces[h.id]
            self.assertGreater(kw.get("standard", 0.0), 0.0, h.id)
            foot = kw.get("height", 1.3) + kw["standard"]
            self.assertGreaterEqual(foot, G.OVERHEAD_CLEARANCE, h.id)
            self.assertAlmostEqual(max(p[1] for fc in lamp.faces for p in fc.pts), foot + 0.72, places=6)

    def test_open_leaves_lie_on_the_wall_face(self):
        leaves = {u.id: u for u in self.roles["leaves"]}
        self.assertEqual(sorted(leaves), ["leaves:eh_floor_east:e:arch:gh_doors",
                                          "leaves:eh_floor_south:s:arch:eh_front_doors"])
        zones = {"eh_front_doors": ("z", 40.0, [(7.2, 11.2), (20.8, 24.8)]),
                 "gh_doors": ("x", 32.0, [(13.2, 16.2), (23.8, 26.8)])}
        for uid, u in leaves.items():
            axis, c, spans = zones[uid.rsplit(":", 1)[1]]
            self.assertEqual(u.kwargs()["mode"], "face")
            for f in self.cb.faces:
                if f.src != uid:
                    continue
                for p in f.pts:
                    d = abs((p[2] if axis == "z" else p[0]) - c)
                    t = p[0] if axis == "z" else p[2]
                    if any(a - 1e-6 <= t <= b + 1e-6 for a, b in spans):
                        self.assertLessEqual(d, G.LEAF_PROUD + 1e-6)
                    else:  # the tympanum, in the reveal
                        self.assertGreater(p[1], 5.0)
            # The plinth breaks under them.
            for pl in self.roles["plinth"]:
                for f in self.cb.faces:
                    if f.src == pl.id and all(abs((p[2] if axis == "z" else p[0]) - c) < 0.2 for p in f.pts):
                        for p in f.pts:
                            t = p[0] if axis == "z" else p[2]
                            self.assertFalse(any(a + 1e-3 < t < b - 1e-3 for a, b in spans), (pl.id, t))

    def test_plinth_breaks_round_the_counter(self):
        for pl in self.roles["plinth"]:
            for f in self.cb.faces:
                if f.src == pl.id and all(abs(p[0]) < 0.2 for p in f.pts):
                    for p in f.pts:
                        self.assertFalse(24.0 < p[2] < 36.0, (pl.id, p))

    def test_owl_exchange_court(self):
        """The court: the 12 m counter replaces the props kit's 8 m one, the
        reach ring (3.5 m round [3.5, 0, 30]) stays clear of every other
        hero piece below 2.4 m."""
        self.assertFalse([f for f in self.cb.faces if f.src == "props:owl_exchange_counter"])
        counter = [f for f in self.cb.faces if f.src == "hero:owl_counter"]
        zs = [p[2] for f in counter for p in f.pts]
        self.assertAlmostEqual(min(zs), 24.0 - 0.07, places=3)
        self.assertAlmostEqual(max(zs), 36.0 + 0.07, places=3)
        for f in self.cb.faces:
            if f.src.startswith("hero:") and f.src != "hero:owl_counter":
                for x, y, z in f.pts:
                    if y < G.INTRUSION_HEIGHT:
                        self.assertGreater(math.hypot(x - 3.5, z - 30.0), 3.5, f.src)

    def test_byte_identical(self):
        again = A.Builder.from_files().build("entrance_hall")
        self.assertEqual(R.to_json(self.cb.uses), R.to_json(again.uses))
        self.assertEqual(self.cb.digest(), again.digest())


class LintDetectsTests(unittest.TestCase):
    """The lint fails loudly on what it must catch."""

    @classmethod
    def setUpClass(cls):
        cls.b = builder()

    def _cb(self):
        return self.b.build("gf_corridor_west")

    def test_intrusion(self):
        cb = self._cb()
        # A 0.4 m deep block 1 m up on the north wall.
        out = []
        geom.box(out, (-15.0, 1.0, 4.0), (-14.5, 1.5, 4.4), "castle_trim|trim:plinth", skip=("-z",),
                 uv_fn=lambda side, pts: [(0.0, 0.3)] * 4)
        cb.faces += [self.b.face(f.pts, f.tag, f.uv, 0.0, "probe", {}, 0) for f in out]
        rep = LN.Report(cb.cell)
        LN.check_intrusion(cb, self.b.layout, rep)
        self.assertTrue(any("probe" in e for e in rep.errors), rep.errors)

    def test_door_box(self):
        cb = self._cb()
        pts = [(-1.0, 1.0, 8.0), (-1.0, 1.0, 9.0), (-1.0, 2.0, 9.0), (-1.0, 2.0, 8.0)]
        cb.faces.append(self.b.face(pts, "castle_wall|world", None, 0.0, "probe", {"castle_wall": "castle_wall_a"}, 0))
        rep = LN.Report(cb.cell)
        LN.check_doors(cb, self.b.layout, rep)
        self.assertTrue(any("probe" in e for e in rep.errors), rep.errors)

    def test_coverage_and_zfight(self):
        cb = self._cb()
        base = [f for f in cb.faces if f.src == "base" and f.normal()[2] > 0.99 and abs(f.pts[0][2] - 4.0) < 1e-6]
        cb.faces.remove(max(base, key=lambda f: f.area()))
        rep = LN.Report(cb.cell)
        LN.check_coverage(cb, self.b, rep)
        self.assertGreater(rep.stats["coverage_missing"], 0)
        cb = self._cb()
        f = next(f for f in cb.faces if f.src.startswith("plinth"))
        cb.faces.append(A.WFace(list(f.pts), f.material, f.mode, f.arg, f.uv, "probe"))
        rep = LN.Report(cb.cell)
        LN.check_zfight(cb, [], rep)
        self.assertGreater(rep.stats["zfight"], 0)

    def test_openings_on_a_railing_edge(self):
        """Castle kit M2 (the railing-edge rule): an opening with no wall
        behind it, a stairwell's railing edge, fails the lint."""
        import dataclasses

        cb = self.b.build("entrance_hall")
        good = next(o for o in cb.plan.openings if o.kind == "niche")
        bad = dataclasses.replace(good, id="probe", surface="eh_floor_ws", run="eh_floor_ws:n0", axis="z", c=22.0, sign=1.0,
                                  t0=6.0, t1=8.0)
        cb.plan.openings.append(bad)
        rep = LN.Report(cb.cell)
        try:
            LN.check_on_wall(cb, self.b, rep)
        finally:
            cb.plan.openings.remove(bad)
        self.assertTrue(any("probe" in e for e in rep.errors), rep.errors)

    def test_uvs_out_of_their_strip(self):
        cb = self._cb()
        f = next(f for f in cb.faces if f.mode == "trim")
        f.uv = [(u, 0.01) for u, _ in f.uv]
        rep = LN.Report(cb.cell)
        LN.check_uvs(cb, self.b, rep)
        self.assertGreater(rep.stats["uv_bad"], 0)


def _synthetic():
    """A hall, a raised floor overlooking it (a railing at the drop), a
    flight between them with a railing on its open side, and a window wall."""
    surfaces = [
        {"id": "low", "kind": "room", "name": "low", "level": 0, "cell": "kc", "min": [0, 0], "max": [10, 10], "y": 0,
         "ceiling": 9.0},
        {"id": "high", "kind": "room", "name": "high", "level": 0, "cell": "kc", "min": [10, 0], "max": [20, 13],
         "y": 3.0, "ceiling": 6.0},
        {"id": "st", "kind": "stair", "name": "st", "level": 0, "cell": "kc", "min": [4, 10], "max": [10, 13], "y": 0,
         "y_top": 3.0, "rise": "+x", "ceiling": 9.0},
    ]
    lay = L.parse_layout({"version": 1, "surfaces": surfaces, "spawns": [{"id": "s", "pos": [2, 0, 2], "yaw": 0}]})
    data = {"version": 1, "camera_max": {"default": 16, "low_cap": 18, "cells": {}}, "cells": {"kc": {
        "budget": "corridor", "materials": {"castle_wall": "castle_wall_b", "castle_floor": "castle_floor_long"},
        "bay_target": 5.0, "arches": [],
        "openings": [{"kind": "window", "piece": "kit_window_lancet_1.6x5", "run": "low:w0", "bays": "all", "sill": 2.4}],
        "lanterns": None, "hero": [], "replaces": []}}}
    plan, rep = S.resolve(data, lay, PROPS_PATH, S.load_texture_set(TEXTURES_PATH))
    assert rep.ok, rep.errors
    b = A.Builder(lay, [], [], set(), plan, S.load_texture_set(TEXTURES_PATH), PROPS_PATH, TEXTURES_PATH)
    return lay, b


class SyntheticCellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lay, cls.b = _synthetic()
        cls.cb = cls.b.build("kc")
        cls.rep = LN.lint_cell(cls.cb, cls.b)
        cls.roles = {}
        for u in cls.cb.uses:
            cls.roles.setdefault(u.role, []).append(u)

    def test_flight_balustrades_newels_quoins(self):
        self.assertEqual(len(self.roles["flight"]), 1)
        self.assertGreaterEqual(len(self.roles["balustrade"]), 2)
        newels = self.roles["newel"]
        self.assertGreaterEqual(len(newels), len(self.roles["balustrade"]) + 1)
        for i, a in enumerate(newels):  # one newel where two railings meet
            for b in newels[i + 1:]:
                self.assertGreater(math.hypot(a.origin[0] - b.origin[0], a.origin[2] - b.origin[2]), 0.35)
        self.assertTrue(any(u.kwargs()["rise"] > 0.5 for u in self.roles["balustrade"]))  # the one on the flight
        self.assertIn("quoin", self.roles)
        self.assertNotIn("floor_stair", {m.material for m in self.cb.meshes.values()})

    def test_lint_clean(self):
        self.assertEqual(self.rep.errors, [])
        self.assertLessEqual(self.rep.stats["tread_error_max"], G.TREAD_RAMP_TOLERANCE)
        self.assertLessEqual(self.rep.stats["intrusion_max"], G.INTRUSION_MAX + 1e-9)

    def test_lod1_for_the_balustrades(self):
        self.assertEqual(sorted(self.cb.lod_meshes), ["kc.castle_trim_lod1"])
        md, base, dist = self.cb.lod_meshes["kc.castle_trim_lod1"]
        self.assertEqual(base, "kc.castle_trim")
        self.assertEqual(dist, R.LOD_DISTANCE)
        self.assertLess(md.triangles(), self.cb.meshes["kc.castle_trim"].triangles())
        self.assertIn("castle_trim", self.cb.library())

    def test_variants_resolve(self):
        mats = {m.material for m in self.cb.meshes.values()}
        self.assertIn("castle_wall_b", mats)
        self.assertIn("castle_floor_long", mats)
        self.assertFalse(any(m in ("castle_wall", "castle_floor") for m in mats))


class GreatHallPieceTests(unittest.TestCase):
    """Castle kit M3 (the Great Hall): the hall's pieces keep their
    triangle budgets (docs/art/castle-kit.md) and the hard rules by
    themselves."""

    @classmethod
    def setUpClass(cls):
        cls.built = {name: P.build(name, **params) for name, params in P.CATALOGUE.items()}

    def tris(self, name):
        return self.built[name].triangles()

    def test_triangle_budgets(self):
        self.assertLessEqual(self.tris("kit_truss_archbraced_22"), 3000)
        for k in "abcd":
            self.assertLessEqual(self.tris(f"kit_beast_brazier_{k}"), 1500)
        self.assertLessEqual(self.tris("kit_fireplace_hooded_4.4x3.6"), 2500)
        self.assertLessEqual(self.tris("kit_window_end_9x12"), 4000)
        self.assertLessEqual(self.tris("kit_window_lancet_3.2x10"), 1000)
        for n in (13, 43):
            self.assertLessEqual(self.tris(f"kit_table_house_{n}"), 600 * n / 12)
            self.assertLessEqual(self.tris(f"kit_bench_{n}"), 300 * n / 12)
            self.assertLessEqual(self.tris(f"kit_table_setting_{n}"), 2500 * n / 43)
        self.assertLessEqual(self.tris("kit_table_staff_16"), 600 * 16 / 12)
        self.assertLessEqual(self.tris("kit_table_setting_16"), 2500 * 16 / 43)
        self.assertLessEqual(self.tris("kit_chair_row_staff"), 300 * 18)
        self.assertLessEqual(self.tris("kit_chair_high"), 600)
        self.assertLessEqual(self.tris("kit_owl_lectern"), 2500)
        self.assertLessEqual(self.tris("kit_hourglass_set"), 3000)
        c = self.built["kit_candles_floating"]
        n = P.CATALOGUE["kit_candles_floating"]["count"]
        self.assertLessEqual(c.triangles(), G.CANDLE_TRIS * n)
        self.assertEqual(sum(len(f.pts) - 2 for f in c.lod1), G.CANDLE_TRIS_LOD1 * n)

    def test_fireplace_rules(self):
        p = self.built["kit_fireplace_hooded_4.4x3.6"]
        low = [pt for f in p.faces for pt in f.pts if pt[1] < G.INTRUSION_HEIGHT - 1e-6]
        self.assertLessEqual(max(pt[2] for pt in low), G.INTRUSION_MAX + 1e-9)  # jambs and fire within the rule
        hood = [pt for f in p.faces for pt in f.pts if pt[2] > G.INTRUSION_MAX + 1e-6]
        self.assertGreaterEqual(min(pt[1] for pt in hood), G.INTRUSION_HEIGHT)  # all the projecting hood above 2.4 m
        self.assertLessEqual(max(pt[2] for pt in hood), G.FIREPLACE_HOOD_PROUD + 1e-9)
        self.assertLessEqual(max(abs(pt[0]) for pt in hood) * 2, G.FIREPLACE_HOOD_WIDTH + 1e-9)
        self.assertLessEqual(max(pt[1] for pt in hood), G.FIREPLACE_HOOD[1] + 1e-9)
        self.assertAlmostEqual(min(pt[2] for f in p.faces for pt in f.pts), -0.7)  # the firebox's depth
        self.assertEqual(len(p.meta["lights"]), 1)
        self.assertTrue(p.meta["collider"])

    def test_wainscot_and_crest_stay_shallow(self):
        for name in ("kit_wainscot", "kit_wainscot_crest"):
            self.assertLessEqual(max(pt[2] for f in self.built[name].faces for pt in f.pts), G.WAINSCOT_PROUD + 1e-9)
        w = self.built["kit_wainscot"]
        self.assertAlmostEqual(max(pt[1] for f in w.faces for pt in f.pts), 3.2)
        s = P.build("kit_wainscot", length=2.0, height=3.2, ends=["none", "none"], rise=0.6)
        self.assertAlmostEqual(max(pt[1] for f in s.faces for pt in f.pts), 3.8)  # its top follows a stair

    def test_braziers_hang_their_basket_at_drop(self):
        shapes = set()
        for k in "abcd":
            p = self.built[f"kit_beast_brazier_{k}"]
            self.assertAlmostEqual(min(pt[1] for f in p.faces for pt in f.pts), -2.5)
            self.assertTrue(p.walkthrough)
            shapes.add(p.triangles())
            tags = {f.tag for f in p.faces}
            self.assertIn(G.palette_tag("glow", "glow_lantern"), tags)  # the fire glows
        self.assertEqual(len(shapes), 4)  # four designs

    def test_braziers_carry_a_camera_shell(self):
        # Integration: a box round bracket, beast and basket keeps the follow camera out.
        for k in "abcd":
            p = self.built[f"kit_beast_brazier_{k}"]
            shell = p.meta["collider"]
            self.assertEqual(len(shell), 5)
            pts = [q for f in p.faces for q in f.pts]
            sp = [q for poly in shell for q in poly]
            for i in range(3):  # it covers the piece (its back is the wall face, z 0)
                if i < 2:
                    self.assertLessEqual(min(q[i] for q in sp), min(q[i] for q in pts) + 1e-9)
                self.assertGreaterEqual(max(q[i] for q in sp), max(q[i] for q in pts) - 1e-9)
            self.assertAlmostEqual(min(q[2] for q in sp), 0.0)

    def test_truss_stays_under_the_sky(self):
        p = self.built["kit_truss_archbraced_22"]
        for f in p.faces:
            for x, y, z in f.pts:
                self.assertLessEqual(y, 5.5 + (11.0 - abs(x)) * 1.0 - 0.05 + 1e-6)  # the sky plane over it
                self.assertLessEqual(abs(x), 11.0 + 1e-9)  # nothing into the side walls
        e = P.build("kit_truss_archbraced_22", plate=5.5, ridge=16.5, engaged=1)
        self.assertGreaterEqual(min(pt[2] for f in e.faces for pt in f.pts), -1e-9)  # flat against its gable
        self.assertTrue(p.meta["collider"])

    def test_staff_setting_follows_the_chairs(self):
        from castle_kit.pieces import hall_furniture as HF

        chairs = self.built["kit_chair_row_staff"].meta["chairs"]
        self.assertEqual(len(chairs), 18)
        self.assertEqual(sorted(chairs + [0.0]), HF.staff_places())
        self.assertEqual(self.built["kit_table_setting_16"].meta["places"], 19)

    def test_candles_in_their_rect(self):
        rect = P.CATALOGUE["kit_candles_floating"]["rect"]
        p = self.built["kit_candles_floating"]
        for f in p.faces:
            for x, y, z in f.pts:
                self.assertTrue(rect[0] - 0.05 <= x <= rect[2] + 0.05 and rect[1] - 0.05 <= z <= rect[3] + 0.05)
        top = max(pt[1] for f in p.faces for pt in f.pts)
        self.assertLessEqual(top, P.CATALOGUE["kit_candles_floating"]["y1"] + G.CANDLE_HEIGHT + 1e-9)
        self.assertGreaterEqual(p.meta["lowest"], P.CATALOGUE["kit_candles_floating"]["y0"] - 1e-9)

    def test_candles_read_from_the_floor(self):
        # Review M3+M4: 6 cm candles with 11 cm flames drew as one- or two-pixel dark dashes
        # from the hall floor (the outline pass ate them); wax and flame are now about 10 cm wide.
        from castle_kit.pieces import hall_furniture as HF

        args = P.CATALOGUE["kit_candles_floating"]
        p = self.built["kit_candles_floating"]
        spots = HF.candle_positions(args["rect"], args["y0"], args["y1"], args["count"], args["seed"])
        self.assertEqual(len(spots), args["count"])
        for x, y, z, h in spots:
            near = lambda q: math.hypot(q[0] - x, q[2] - z) < 0.2 and y - 1e-6 <= q[1] <= y + G.CANDLE_HEIGHT + 1e-6
            wax = [q for f in p.faces if ":wax" in f.tag for q in f.pts if near(q)]
            self.assertTrue(wax)
            self.assertGreaterEqual(max(q[0] for q in wax) - min(q[0] for q in wax), 0.09)
            self.assertGreaterEqual(max(q[1] for q in wax) - min(q[1] for q in wax), 0.28 - 1e-9)
            # Animated fire: the flame is the client's card on the wick (meta "candles"), 15 cm tall.
            flame = [c for c in p.meta["candles"] if math.hypot(c[0] - x, c[2] - z) < 1e-6]
            self.assertEqual(len(flame), 1)
            self.assertAlmostEqual(flame[0][1], y + h + 0.005, places=3)
            self.assertGreaterEqual(flame[0][3], 0.15)
            self.assertLessEqual(flame[0][1] + flame[0][3] - y, G.CANDLE_HEIGHT + 1e-9)
        self.assertFalse([f for f in p.faces if "glow_candle" in f.tag])
        top1 = max(pt[1] for f in p.lod1 for pt in f.pts)
        self.assertLessEqual(top1, args["y1"] + G.CANDLE_HEIGHT + 1e-9)


class GreatHallTests(unittest.TestCase):
    """Castle kit M3: the Great Hall built (docs/art/castle-kit.md)."""

    @classmethod
    def setUpClass(cls):
        cls.b = builder()
        cls.cb = cls.b.build("great_hall")
        cls.rep = LN.lint_cell(cls.cb, cls.b)
        cls.roles = {}
        for u in cls.cb.uses:
            cls.roles.setdefault(u.role, []).append(u)

    def test_lint_is_clean(self):
        self.assertEqual(self.rep.errors, [])
        st = self.rep.stats
        self.assertLessEqual(st["intrusion_max"], G.INTRUSION_MAX + 1e-9)
        self.assertGreaterEqual(st["overhead_min"], G.OVERHEAD_CLEARANCE)
        self.assertGreaterEqual(st["overhead_m3_min"], G.OVERHEAD_CLEARANCE)
        self.assertEqual(st["doors_checked"], 1)
        self.assertLessEqual(st["tread_error_max"], G.TREAD_RAMP_TOLERANCE)
        for k in ("coverage_missing", "zfight", "degenerate", "uv_bad", "window_depth_bad", "off_wall", "keep_clear_hits"):
            self.assertEqual(st[k], 0, k)

    def test_budget_draws_and_materials(self):
        cb = self.cb
        b = G.BUDGETS["great_hall"]
        # castle M5c: + its @ext wall clone and castle_roof (the facade); + 4 hourglass
        # sand nodes and 2 nodes (cloth, rods) per house's cup banners, of which the budget counts one house.
        self.assertEqual(cb.draws(), 14 + 4 + 4 * 2)
        self.assertEqual(A.budget_draws(cb), 14 + 4 + 2)
        self.assertLessEqual(A.budget_draws(cb), G.draw_cap("great_hall", "great_hall"))
        self.assertLessEqual(cb.triangles(), b.tris[1])
        self.assertEqual(cb.library(), ["castle_cloth", "castle_floor_long", "castle_glass", "castle_props", "castle_roof",
                                        "castle_sky", "castle_trim", "castle_wall_b", "castle_wall_b@ext",
                                        "castle_wood"])  # no castle_ceiling
        groups = sorted(n for n in cb.meshes if n.count(".") == 2)
        candles = [f"great_hall.castle_props.candles_{k}" for k in range(4)]
        self.assertEqual(groups, sorted(candles + [f"great_hall.{m}.cup_banners_house_{x}" for x in "abcd"
                                                   for m in ("castle_cloth", "castle_props")]
                                        + [f"great_hall.castle_props.hourglass_sand_house_{x}" for x in "abcd"]))
        self.assertEqual(sorted(cb.lod_meshes), sorted([f"{g}_lod1" for g in candles]
                                                       + ["great_hall.castle_roof_lod1", "great_hall.castle_wall_b@ext_lod1"]))
        self.assertLessEqual(cb.collider_triangles(), G.COLLIDER_TRIS)
        self.assertTrue(all(f.material.startswith("castle_") for f in cb.faces))

    def test_candle_groups_move(self):
        cb = self.cb
        for k, cg in enumerate(cb.plan.candles):
            name = f"great_hall.castle_props.candles_{k}"
            anim = cb.extras[name][G.EXTRAS_ANIM]
            self.assertEqual((anim["kind"], anim["group"], anim["amp"], anim["period"]), ("bob", cg.id, cg.amp, cg.period))
            self.assertEqual(cb.lod_meshes[name + "_lod1"][2], cg.lod1)
            u = next(u for u in self.roles["candles"] if u.group == cg.id)
            self.assertEqual(cb.pieces[u.id].meta["count"], cg.count)
            self.assertLessEqual(cb.meshes[name].triangles(), G.CANDLE_TRIS * cg.count)
        self.assertNotIn("great_hall.castle_cloth", cb.extras)


    def test_sky_replaces_the_ceiling(self):
        cb = self.cb
        sky = [f for f in cb.faces if f.material == "castle_sky"]
        self.assertEqual(len(sky), 18)
        for f in sky:
            self.assertTrue(all(0.0 <= a <= 1.0 and 0.0 <= b <= 1.0 for a, b in f.uv))
            self.assertLess(f.normal()[1], 0.0)  # faces the hall below
        self.assertFalse([f for f in cb.faces if f.material == "castle_ceiling"])
        # The collider keeps the Planner's ceiling and adds the sky's shell.
        shell = [p for p in cb.collider if all(17.5 - 1e-6 <= q[1] <= 28.5 + 1e-6 for q in p) and len(p) == 4
                 and len({round(q[1], 3) for q in p}) == 2]
        self.assertGreaterEqual(len(shell), 18)

    def test_lines_trusses_and_beasts(self):
        self.assertEqual(len(self.roles["line_shaft"]), 16)
        self.assertEqual(len(self.roles["corbel"]), 16)
        for u in self.roles["corbel"]:
            self.assertAlmostEqual(u.origin[1], 12.0)
        heroes = {u.id.split(":", 1)[1]: u for u in self.roles["hero"]}
        self.assertEqual(sum(1 for h in heroes if h.startswith("truss_")), 9)
        north = [heroes[f"beast_n{k}"].piece[-1] for k in range(1, 8)]
        south = [heroes[f"beast_s{k}"].piece[-1] for k in range(1, 8)]
        self.assertEqual("".join(north), "abcdabc")
        self.assertEqual("".join(south), "dcbadcb")
        self.assertNotIn("plinth", self.roles)  # the wainscot takes its place
        self.assertTrue(self.roles["wainscot"])
        for u in self.roles["cornice"]:
            self.assertAlmostEqual(u.origin[1], 17.5)  # one wall plate, the dais and the steps too
        self.assertEqual(len(self.roles["ridge"]), 1)

    def test_dueling_club_kept_clear(self):
        flights = [u.run for u in self.roles["flight"]]
        self.assertEqual(flights, ["gh_dais_steps"])  # the stage's steps are its own kit's
        props = {f.src for f in self.cb.faces if f.src.startswith("props:")}
        for kid in ("dc_stage", "dc_area", "dc_bench_nw", "dc_bench_ne", "dc_bench_sw", "dc_bench_se", "dc_banner_nw"):
            self.assertIn(f"props:{kid}", props)
        self.assertNotIn("props:dc_candles", props)  # the candle groups replace it

    def test_seats(self):
        seats = self.cb.seats
        self.assertEqual(len(seats), 8 + 18 + 1 + 4)  # house benches, staff chairs, the high chair, spectator benches
        for x0, z0, x1, z1, y, yaw, front, back, drop in seats:
            self.assertTrue(x0 < x1 and z0 < z1 and 0.3 < y < 1.3 and -math.pi < yaw <= math.pi + 1e-4)
            self.assertTrue(0.0 < front <= G.SEAT_FRONT_MAX and 0.0 < back and 0.3 < drop <= y)
        bench = next(r for r in seats if abs(r[1] - 10.45) < 1e-6)
        self.assertEqual(bench, [38.5, 10.45, 81.5, 10.85, 0.45, 0.0, 0.2, 0.2, 0.45])
        staff = [r for r in seats if r[0] > 97.0]
        self.assertTrue(all(abs(r[5] + math.pi / 2) < 1e-3 for r in staff))  # facing the hall

    def test_fireplace_light(self):
        lights = [lt for lt in self.cb.lights if "fireplace" in lt["id"]]
        self.assertEqual(len(lights), 1)
        self.assertLessEqual(len(self.cb.lights), kit.LIGHTS_PER_CELL)

    def test_byte_identical(self):
        again = A.Builder.from_files().build("great_hall")
        self.assertEqual(R.to_json(self.cb.uses), R.to_json(again.uses))
        self.assertEqual(self.cb.digest(), again.digest())
        self.assertEqual(self.cb.seats, again.seats)

    def test_other_cells_untouched_by_the_m3_rules(self):
        cp = self.b.kit_plan.cells["entrance_hall"]
        ctx = R.context(self.b.layout, cp, self.b.kit_entries, self.b.open_edges)
        uses = [u for u in R.placements(ctx) if u.role != "hero"]
        self.assertIs(R.great_hall_m3(ctx, uses), uses)


class HouseCupHallTests(unittest.TestCase):
    """The hourglass sand nodes and the cup banner groups in
    the Great Hall (docs/art/castle-kit.md)."""

    @classmethod
    def setUpClass(cls):
        cls.b = builder()
        cls.cb = cls.b.build("great_hall")

    def test_sand_nodes_fill_from_their_base(self):
        cb = self.cb
        xs = []
        for x in "abcd":
            name = f"great_hall.castle_props.hourglass_sand_house_{x}"
            md = cb.meshes[name]
            self.assertEqual(cb.extras[name][G.EXTRAS_ANIM], {"kind": "fill", "house": f"house_{x}",
                                                               "group": f"hourglass_sand_house_{x}"})
            lo = min(v[1] for v in md.verts)
            self.assertAlmostEqual(md.origin[1], lo)  # origin at the base: a Y scale of 0..1 fills it
            self.assertGreater(max(v[1] for v in md.verts) - lo, 2.0)  # modelled full
            self.assertAlmostEqual(md.origin[0], (min(v[0] for v in md.verts) + max(v[0] for v in md.verts)) / 2)
            # Every face is the house's palette colour (props atlas `@house_<x>`).
            self.assertTrue(all(f.arg.endswith(f"@house_{x}") for f in md.src_faces))
            self.assertLessEqual(md.triangles(), 40)
            xs.append(md.origin[0])
        self.assertEqual(xs, sorted(xs))  # left to right in House::ALL order
        # The glass the sand stands over is pale glass all the way up (no static sand left in it).
        hero = next(u for u in cb.uses if u.id == "hero:hourglasses")
        self.assertEqual(sorted(cb.pieces[hero.id].meta["sand"]), [f"hourglass_sand_house_{x}" for x in "abcd"])

    def test_cup_banner_groups(self):
        cb = self.cb
        for x in "abcd":
            for mat in ("castle_cloth", "castle_props"):
                name = f"great_hall.{mat}.cup_banners_house_{x}"
                self.assertEqual(cb.extras[name][G.EXTRAS_ANIM], {"kind": "cup_banners", "house": f"house_{x}",
                                                                   "group": f"cup_banners_house_{x}"})
                self.assertIsNone(cb.meshes[name].origin)
            cloth = cb.meshes[f"great_hall.castle_cloth.cup_banners_house_{x}"]
            self.assertTrue(all(f.arg == f"banner_house_{x}" for f in cloth.src_faces))
            self.assertEqual(len({round(f.pts[0][0], 3) for f in cloth.src_faces}), 8)  # eight along the hall
            tris = cloth.triangles() + cb.meshes[f"great_hall.castle_props.cup_banners_house_{x}"].triangles()
            self.assertLessEqual(tris, G.CUP_BANNER_TRIS)
            # Over the long tables, high above the candles, under the wall plate.
            ys = [v[1] for v in cloth.verts]
            self.assertGreater(min(ys), 9.0 + 0.5)
            self.assertLessEqual(max(ys), 17.5)
            for v in cloth.verts:  # the long tables' lines, z 11.8 and 28.2
                self.assertLessEqual(min(abs(v[2] - 11.8), abs(v[2] - 28.2)), G.CUP_BANNER_SIZE[0] / 2 + 1e-6)
        # Each house on its own x: a tie hangs its banners side by side, never in one plane.
        planes = {}
        for x in "abcd":
            cloth = cb.meshes[f"great_hall.castle_cloth.cup_banners_house_{x}"]
            planes[x] = {round(v[0], 3) for v in cloth.verts}
        for a in "abcd":
            for b_ in "abcd":
                if a < b_:
                    self.assertFalse(planes[a] & planes[b_])

    def test_hidden_banners_do_not_occlude(self):
        from castle_kit import shade as SH

        for f in self.cb.faces:
            if G.is_cup_banner_group(f.group) or G.is_sand_group(f.group):
                self.assertFalse(SH.occludes(f))

    def test_common_room_c_notice_board(self):
        cb = self.b.build("common_room_house_c")
        u = next(u for u in cb.uses if u.id == "hero:noticeboard")
        self.assertEqual(u.piece, G.NOTICEBOARD)
        self.assertNotIn("hero:bookcase_s2", {v.id for v in cb.uses})
        lay = json.load(open(LAYOUT_PATH))
        it = next(i for i in lay["interactables"] if i["id"] == "notice_board_house_c")
        # The interactable stands about 1 m in front of the board, facing it.
        self.assertAlmostEqual(it["pos"][0], u.origin[0], delta=0.3)
        self.assertAlmostEqual(abs(it["pos"][2] - u.origin[2]), 1.0, delta=0.3)
        self.assertEqual(LN.lint_cell(cb, self.b).errors, [])


class AtlasTests(unittest.TestCase):
    def test_kit_slots_exist_and_uvs_do_not_move(self):
        cells, atlas = PA.build(PROPS_PATH, TEXTURES_PATH)
        bare, plain = kit.assemble(kit.load_kit(PROPS_PATH))
        self.assertEqual(atlas.size, plain.size)
        for cid in bare:
            self.assertEqual(cells[cid].uvs, bare[cid].uvs, cid)
        from castle_kit.pieces import fixtures

        for tile, colour in fixtures.SLOTS:
            atlas.index(tile, colour)
        for mat in ("glow_sun", "prop_stone_dark", "prop_canvas", "fabric_house_a"):
            atlas.index(*A.greybox_slot(mat))

    def test_placeholders(self):
        self.assertEqual(A.placeholder_colour("castle_props"), (1.0, 1.0, 1.0))
        self.assertEqual(A.placeholder_colour("castle_wall_c"), L.MATERIALS["wall"][0])
        self.assertEqual(A.placeholder_colour("castle_floor_square"), L.MATERIALS["floor_corridor"][0])
        self.assertEqual(A.placeholder_colour("castle_glass"), L.MATERIALS["glow_sky"][0])


if __name__ == "__main__":
    unittest.main()


# -- castle kit M4: the Grand Staircase (docs/art/castle-kit.md "Portraits and tapestries") -------------------


def _gate_covers(sid, axis, c, ta, tb) -> bool:
    """Does a landing gate of data/world/stairs.json span this whole railing run?"""
    for f in R.stairs_file()["flights"]:
        for g in f["gates"]:
            span = R._gate_span(axis, g)
            if g["landing"] == sid and span is not None and abs(span[0] - c) < 1e-6 \
                    and span[1] <= ta + 1e-6 and span[2] >= tb - 1e-6:
                return True
    return False


class GrandStaircaseTests(unittest.TestCase):
    """The two Grand Staircase cells and the seventh-floor corridor built:
    lint clean, the two-cell budget with the drawn estimate, gallery edges,
    landings, the pier stack, stepped flights, spot lanterns, portrait
    clusters in their bands and the guardian portrait."""

    CELLS = ("grand_staircase", "grand_staircase_upper")

    @classmethod
    def setUpClass(cls):
        cls.b = builder()
        cls.cbs = {c: cls.b.build(c) for c in cls.CELLS + ("l7_corridor_north",)}
        cls.reps = {c: LN.lint_cell(cb, cls.b) for c, cb in cls.cbs.items()}
        cls.group = LN.lint_budget_group([cls.cbs[c] for c in cls.CELLS], cls.b.layout)

    def uses(self, role, cells=CELLS):
        return [u for c in cells for u in self.cbs[c].uses if u.role == role]

    def test_lint_is_clean(self):
        for c, rep in self.reps.items():
            self.assertEqual(rep.errors, [], c)
            st = rep.stats
            self.assertLessEqual(st["intrusion_max"], G.INTRUSION_MAX + 1e-9, c)
            self.assertGreaterEqual(st["overhead_min"], G.OVERHEAD_CLEARANCE, c)
            self.assertLessEqual(st["tread_error_max"], G.TREAD_RAMP_TOLERANCE, c)
            for k in ("coverage_missing", "zfight", "degenerate", "uv_bad", "off_wall"):
                self.assertEqual(st[k], 0, (c, k))
            self.assertLessEqual(st["collider_tris"], G.COLLIDER_TRIS, c)
        self.assertEqual(self.group.errors, [])
        self.assertEqual(self.group.stats["zfight_across"], 0)

    def test_two_cell_budget_and_drawn_estimate(self):
        b = G.BUDGETS["grand_staircase"]
        st = self.group.stats
        self.assertLessEqual(st["draws"], b.draws + G.FACADE_DRAWS)
        # castle M5c: + the facade's @ext wall and its storeys' @ext trim; review: + the blind windows' glass;
        # + the three moving flights' swing nodes (grid.P3C_GS_DRAWS_ADD).
        self.assertEqual(st["draws"], 22 + G.P3C_GS_DRAWS_ADD)
        self.assertLessEqual(st["tris"], b.tris[1])
        self.assertLessEqual(st["drawn_worst"], b.drawn)
        est = LN.drawn_estimate([self.cbs[c] for c in self.CELLS], self.b.layout)
        # Every gallery, floor and landing is an eye; LOD1 makes far bands cheap.
        self.assertGreater(len(est["eyes"]), 40)
        self.assertLess(min(est["eyes"].values()), sum(cb.triangles() for c, cb in self.cbs.items() if c in self.CELLS))

    def test_meshes_and_bands(self):
        lo, hi = self.cbs["grand_staircase"], self.cbs["grand_staircase_upper"]
        self.assertEqual(sorted(lo.meshes), sorted(
            ["grand_staircase.castle_ceiling", "grand_staircase.castle_floor_square", "grand_staircase.castle_props",
             "grand_staircase.castle_trim", "grand_staircase.castle_wall_c",  # castle M5a: the darker stone
             "grand_staircase.castle_trim@ext", "grand_staircase.castle_wall_c@ext",  # castle M5c: the facade
             "grand_staircase.castle_glass"]  # castle M5c review: the west range's blind windows
            + [f"grand_staircase.castle_portraits_a.l{k}" for k in range(4)]))
        self.assertEqual(sorted(hi.meshes), sorted(
            [f"grand_staircase_upper.{m}" for m in ("castle_ceiling", "castle_floor_square", "castle_glass", "castle_props",
                                                    "castle_trim", "castle_wall_c")]
            + [f"grand_staircase_upper.castle_portraits_b.l{k}" for k in range(4, 8)]
            # The moving flights' swing nodes.
            + [G.SWING_NODE.format(cell="grand_staircase_upper", flight=f) for f in ("flight_3_4", "flight_5_6", "flight_6_7")]))
        for cb in (lo, hi):
            for name in cb.meshes:
                if ".castle_portraits_" in name:
                    lod = cb.lod_meshes[G.lod1_name(name)]
                    self.assertEqual(lod[2], G.LOD_STOREY)
                    self.assertNotIn(G.lod1_name(name), cb.extras)
            self.assertNotIn("castle_wood", cb.library())
            self.assertNotIn("castle_cloth", cb.library())

    def test_gallery_edges_on_every_flat_railing_run(self):
        # A run a landing gate closes is split round the gate (`<id>:<k>`).
        edges = {u.id for u in self.uses("gallery_edge")}
        runs = {e if e.count(":") == 3 else e.rsplit(":", 1)[0] for e in edges}
        flat = 0
        for c in self.CELLS:
            ctx = R.context(self.b.layout, self.b.kit_plan.cells[c], self.b.kit_entries, self.b.open_edges)
            for (sid, axis, cc, sign), spans in ctx.railings.items():
                if self.b.layout.by_id(sid).sloped:
                    continue
                for i, (ta, tb) in enumerate(spans):
                    if tb - ta >= R.EDGE_MIN:
                        rid = f"gallery_edge:{sid}:{axis}{R._r(cc)}:{i}"
                        if rid not in runs and _gate_covers(sid, axis, cc, ta, tb):
                            continue  # A gate's whole edge (the sixth-floor corbel)
                        flat += 1
                        self.assertIn(rid, runs)
        self.assertEqual(len(runs), flat)
        # Flights keep the gothic balustrade; flat runs have no plain balustrade left.
        for u in self.uses("balustrade"):
            self.assertTrue(self.b.layout.by_id(u.run).sloped, u.id)
        tris = sum(self.cbs[c].pieces[u.id].triangles() for c in self.CELLS for u in self.cbs[c].uses if u.role == "gallery_edge")
        lod1 = sum(sum(len(f.pts) - 2 for f in self.cbs[c].pieces[u.id].lod1) for c in self.CELLS
                   for u in self.cbs[c].uses if u.role == "gallery_edge")
        length = sum(u.kwargs()["length"] for u in self.uses("gallery_edge"))
        self.assertLessEqual(tris / length, 80)
        self.assertLessEqual(lod1 / length, 25)

    def test_gallery_edge_dressing_keeps_clear_over_flights(self):
        """Where a landing or gallery bridges a flight, the dressing hangs no
        deeper than the 2.4 m rule allows (the lint's intrusion check passes
        too); over the solid pier it is left out."""
        from castle_kit.pieces import staircase as ST

        variants = {}
        for u in self.uses("gallery_edge"):
            for _, _, var in u.kwargs()["drops"]:
                variants[var] = variants.get(var, 0) + 1
        self.assertGreater(variants.get("full", 0), 20)
        self.assertIn("none", variants)  # the first pier landing over the solid pier
        u = next(u for u in self.uses("gallery_edge") if u.id.startswith("gallery_edge:gs_landing_1_2_a:x-48.0"))
        deepest = max(ST.EDGE_DROPS[v] for _, _, v in u.kwargs()["drops"])
        self.assertLessEqual(deepest, ST.EDGE_DROPS["plain"])

    def test_every_landing_surface_has_one_corbel_landing(self):
        heroes = [h for c in self.CELLS for h in self.b.kit_plan.cells[c].hero if h.piece.startswith("kit_landing_corbel")]
        # The balconies and the corbel the moving flights reach at B are carried too.
        landings = [s for s in self.b.layout.surfaces if s.id.startswith(("gs_landing_", "gs_balcony_", "gs_corbel_"))]
        self.assertEqual(len(heroes), len(landings))
        for s in landings:
            on = [h for h in heroes if s.x0 - 1e-6 <= h.pos[0] <= s.x1 + 1e-6 and s.z0 - 1e-6 <= h.pos[2] <= s.z1 + 1e-6
                  and abs(h.pos[1] - s.y) < 1e-6]
            self.assertEqual(len(on), 1, s.id)

    def test_no_pier_landing_floats(self):
        """Every pier landing above the first stands on colonnettes that reach
        its slab's underside from the landing below: four, less the corners a
        moving half-flight sweeps past (two at least)."""
        from castle_kit.pieces import staircase as ST

        cols = R.colonnettes(self.b.kit_plan.cells["grand_staircase"])
        piers = sorted((s for s in self.b.layout.surfaces if s.id.startswith("gs_pier_")), key=lambda s: s.y)
        self.assertEqual(len(piers), 3)
        for lower, upper in zip(piers, piers[1:]):
            mine = [c for c in cols if abs(c[2] - lower.y) < 1e-6]
            self.assertIn(len(mine), (2, 3, 4))  # 3-4 and 5-6 both sweep past the middle span
            for x, z, y0, y1 in mine:
                self.assertAlmostEqual(y1, upper.y - ST.SLAB)
                # At most 0.25 m into the landing, never into the 4 m flights.
                inside = min(x + ST.COLONNETTE / 2 - lower.x0, lower.x1 - (x - ST.COLONNETTE / 2))
                self.assertLessEqual(min(inside, ST.COLONNETTE), G.INTRUSION_MAX + 1e-9)
                self.assertTrue(x + ST.COLONNETTE / 2 <= -48.0 + 1e-6 or x - ST.COLONNETTE / 2 >= -44.0 - 1e-6)
        pier = P.build("kit_pier_stack_4.5", top=49.5)
        self.assertLessEqual(pier.triangles(), 2500)
        self.assertTrue(pier.meta["collider"])

    def test_flights_hang_with_stepped_undersides(self):
        flights = self.uses("flight")
        self.assertEqual(len(flights), 10)
        solid = [u for u in flights if u.piece == "kit_stair_flight"]
        self.assertEqual([u.run for u in solid], ["gs_flight_0_1"])  # the only one on the ground floor
        for u in flights:
            if u.piece == "kit_stair_flight_stepped":
                p = self.cbs["grand_staircase" if u in self.cbs["grand_staircase"].uses else "grand_staircase_upper"].pieces[u.id]
                self.assertTrue(p.meta["stepped"])
                self.assertEqual(p.meta["steps"], round(u.kwargs()["rise"] / G.RISER))

    def test_spot_lanterns(self):
        lanterns = [u for u in self.uses("lantern")]
        self.assertEqual(len(lanterns), 48)  # six a level
        for u in lanterns:
            self.assertGreaterEqual(u.origin[1] - math.floor(u.origin[1] / G.STOREY) * G.STOREY, 3.6 - 1e-6)

    def test_portrait_clusters(self):
        from castle_kit.pieces import frames as FR

        n = 0
        for c in self.CELLS:
            cp = self.b.kit_plan.cells[c]
            ctx = R.context(self.b.layout, cp, self.b.kit_entries, self.b.open_edges)
            laid = R.portrait_frames(ctx)
            sheet = cp.materials["castle_portraits"]
            placed = {}
            for pc in cp.portraits:
                fr = laid[pc.id]
                n += len(fr)
                lo, hi = G.PORTRAIT_FILL
                self.assertTrue(lo <= FR.coverage(fr, pc.t0, pc.t1, pc.y0, pc.y1) <= hi, pc.id)
                keep = R.portrait_keep_out(ctx, pc.axis, pc.c, pc.sign)
                for i, (ta, yb, w, h, shape, canvas) in enumerate(fr):
                    self.assertGreaterEqual(ta, pc.t0 + G.PORTRAIT_EDGE - 1e-6)
                    self.assertLessEqual(ta + w, pc.t1 - G.PORTRAIT_EDGE + 1e-6)
                    self.assertGreaterEqual(yb, pc.y0 + G.PORTRAIT_EDGE - 1e-6)
                    self.assertLessEqual(yb + h, pc.y1 - G.PORTRAIT_EDGE + 1e-6)
                    self.assertNotEqual((sheet, canvas), G.GUARDIAN_CANVAS)
                    for k in keep:
                        self.assertFalse(ta < k[1] and k[0] < ta + w and yb < k[3] and k[2] < yb + h, pc.id)
                    for tb, yc, w2, h2, _, _ in fr[i + 1:]:
                        apart = max(tb - (ta + w), ta - (tb + w2), yc - (yb + h), yb - (yc + h2))
                        self.assertGreaterEqual(apart, G.PORTRAIT_GAP - 1e-6, pc.id)
                    face = (pc.axis, round(pc.c, 6), pc.sign)
                    for c2, t2, y2 in placed.get(face, []):
                        if c2 == canvas:
                            self.assertGreaterEqual(math.hypot(t2 - (ta + w / 2), y2 - (yb + h / 2)), 6.0 - 1e-6)
                    placed.setdefault(face, []).append((canvas, ta + w / 2, yb + h / 2))
        self.assertTrue(1500 <= n <= 2300, n)

    def test_frames_budget(self):
        for shape in G.PORTRAIT_SHAPES:
            for w, h, _, _ in G.PORTRAIT_FRAMES:
                p = P.build(G.piece_name("portrait", None, w, h, shape))
                self.assertLessEqual(p.triangles(), G.PORTRAIT_TRIS, (w, h, shape))
                self.assertEqual(sum(len(f.pts) - 2 for f in p.lod1), G.PORTRAIT_TRIS_LOD1)
                self.assertLessEqual(max(pt[2] for f in p.faces for pt in f.pts), G.PORTRAIT_PROUD + 1e-9)
                self.assertGreater(min(pt[2] for f in p.faces for pt in f.pts if f.normal()[2] > 0.99), 0.0)

    def test_guardian(self):
        cb = self.cbs["l7_corridor_north"]
        g = cb.pieces["hero:entrance_cr_a_portrait"]
        self.assertTrue(g.walkthrough)
        self.assertLessEqual(g.triangles(), 1200)
        canvas = [f for f in cb.faces if f.src == "hero:entrance_cr_a_portrait" and f.material == "castle_portraits_b"]
        self.assertTrue(canvas)
        self.assertTrue(all(f.arg == f"canvas_{G.GUARDIAN_CANVAS[1]}" for f in canvas))
        self.assertIn("l7_corridor_north.castle_portraits_b.entrance_cr_a_portrait", cb.meshes)  # A moving node
        self.assertLessEqual(cb.draws(), G.BUDGETS["corridor"].draws)
        # Inside the arch of the common-room door (3 x 4), behind the wall face.
        door = self.b.layout.by_id("cr_a_portrait")
        for f in cb.faces:
            if f.src == "hero:entrance_cr_a_portrait":
                for x, y, z in f.pts:
                    self.assertTrue(door.x0 - 1e-6 <= x <= door.x1 + 1e-6)
                    self.assertLessEqual(z, door.z1 + 1e-6)

    def test_hero_pieces_budgets(self):
        for name, cap in (("kit_landing_corbel_4.5x4", 1500), ("kit_landing_corbel_3.5x4", 1500),
                          ("kit_skylight_lantern", 2000), ("kit_floor_compass_12", 400)):
            p = P.build(name, **P.CATALOGUE[name])
            self.assertLessEqual(p.triangles(), cap, name)
        sky = P.build("kit_skylight_lantern", w=12.0, d=16.0)
        self.assertLess(max(pt[1] for f in sky.faces for pt in f.pts), 0.0)  # nothing on the ceiling's plane
        for f in P.build("kit_floor_compass_12", inner=3.4).faces:
            self.assertTrue(all(abs(pt[1]) < 1e-9 for pt in f.pts))  # flush

    def test_compass_cuts_the_floor(self):
        cb = self.cbs["grand_staircase"]
        cut = R.floor_cuts(cb.plan)
        self.assertEqual(cut, [(-52.0, -4.0, -40.0, 8.0)])
        # Only the ground floor's; the pier landing 13.5 m over the compass keeps its floor.
        self.assertEqual(R.floor_cuts(cb.plan, 0.0), cut)
        self.assertEqual(R.floor_cuts(cb.plan, 13.5), [])
        x0, z0, x1, z1 = cut[0]
        for f in cb.faces:
            if (f.src in ("floor",) or f.src.startswith("floor_border")) and max(p[1] for p in f.pts) < 0.5:
                cx = sum(p[0] for p in f.pts) / len(f.pts)
                cz = sum(p[2] for p in f.pts) / len(f.pts)
                self.assertFalse(x0 + 1e-6 < cx < x1 - 1e-6 and z0 + 1e-6 < cz < z1 - 1e-6, f.src)

    def test_placements_byte_identical(self):
        for c in self.CELLS:
            cp = self.b.kit_plan.cells[c]
            one = R.to_json(R.placements(R.context(self.b.layout, cp, self.b.kit_entries, self.b.open_edges)))
            two = R.to_json(R.placements(R.context(self.b.layout, cp, self.b.kit_entries, self.b.open_edges)))
            self.assertEqual(one, two)

    def test_balustrade_and_newel_shells(self):
        """M2 leftover 3: the pierced balustrades and the newels carry coarse
        camera shells (the Entrance Hall's included)."""
        self.assertEqual(len(P.build("kit_balustrade_gothic", length=3.0).meta["collider"]), 3)
        self.assertEqual(len(P.build("kit_newel_ball").meta["collider"]), 5)
        eh = self.b.build("entrance_hall")
        self.assertLessEqual(eh.collider_triangles(), G.COLLIDER_TRIS)


# -- Castle D1: the edge loops and the vertex-AO bake (docs/art/castle-kit.md "Shading: the AO bake") ------


from castle_kit import shade as SH  # noqa: E402
from castle_kit import slice as SL  # noqa: E402

D1_CELLS = ("gf_corridor_west", "l7_corridor_north")


class D1LoopTests(unittest.TestCase):
    """The loops: world faces only, world-aligned, within the loop budget,
    the mapping unmoved, every other face untouched."""

    @classmethod
    def setUpClass(cls):
        cls.b = builder()
        cls.cbs = {c: cls.b.build(c) for c in D1_CELLS}
        cls.b.d1_slice = False
        try:
            cls.plain = {c: cls.b.build(c) for c in D1_CELLS}
        finally:
            cls.b.d1_slice = True

    def test_within_the_loop_budget(self):
        for cell, cb in self.cbs.items():
            st = cb.slice_stats
            self.assertEqual(st.unsliced, self.plain[cell].triangles(), cell)
            self.assertGreater(st.added, 0, cell)
            self.assertLessEqual(st.added, A.Builder.slice_budget(cb.plan.budget, st.unsliced), cell)
            self.assertLessEqual(st.added, G.slice_allowance(cb.plan.budget, st.unsliced), cell)
            self.assertEqual(cb.triangles() - self.plain[cell].triangles(), st.added, cell)
            self.assertEqual(cb.draws(), self.plain[cell].draws(), cell)  # D1 adds no draws
        self.assertEqual(A.Builder.slice_budget("grand_staircase", 69_365), G.SLICE_DRAWN_GROWTH // 2)
        self.assertEqual(A.Builder.slice_budget("corridor", 4_753), G.SLICE_GROWTH_FLOOR)

    def test_only_world_faces_are_cut(self):
        for cell, cb in self.cbs.items():
            key = lambda f: (f.material, f.mode, f.src, f.lod, tuple(tuple(_r(c) for c in p) for p in f.pts))  # noqa: E731
            self.assertEqual(sorted(key(f) for f in cb.faces if f.mode != "world"),
                             sorted(key(f) for f in self.plain[cell].faces if f.mode != "world"), cell)
            area = {}
            for f in cb.faces:
                if f.mode == "world":
                    area[f.material] = area.get(f.material, 0.0) + f.area()
            for f in self.plain[cell].faces:
                if f.mode == "world":
                    area[f.material] -= f.area()
            for mat, d in area.items():
                self.assertAlmostEqual(d, 0.0, places=4, msg=f"{cell} {mat}")

    def test_mapping_does_not_move(self):
        for cell, cb in self.cbs.items():
            for f in cb.faces:
                if f.mode != "world":
                    continue
                want = self.b.uvs(f.material, "world", None, f.pts, None, 0.0, 0)
                for (u, v), (a, b) in zip(f.uv, want):
                    self.assertAlmostEqual(u, a, places=9)
                    self.assertAlmostEqual(v, b, places=9)

    def test_pieces_tile_their_faces(self):
        """Every source face's pieces cover exactly its area in its own plane:
        the cuts only add edges."""
        for cell, cb in self.cbs.items():
            area = {}
            for sign, faces in ((1.0, cb.faces), (-1.0, self.plain[cell].faces)):
                for f in faces:
                    if f.mode == "world":
                        k = (f.material, f.src, f.lod, SL.plane_key(f.pts)[0])
                        area[k] = area.get(k, 0.0) + sign * f.area()
            for k, d in area.items():
                self.assertAlmostEqual(d, 0.0, places=4, msg=f"{cell} {k}")

    def test_stats_add_up(self):
        for cell, cb in self.cbs.items():
            st = cb.slice_stats
            self.assertGreater(st.planes, 0, cell)
            self.assertGreaterEqual(st.pieces, 2 * st.sliced, cell)
            self.assertLessEqual(st.sliced, st.faces, cell)
            self.assertEqual(st.faces, sum(1 for f in self.plain[cell].faces if f.mode == "world"), cell)

    def test_split_and_cut(self):
        quad = [(0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0), (0.0, 3.0, 0.0)]
        a, b = SL.split(quad, 1, 1.5)
        self.assertAlmostEqual(geom.norm(geom.newell(a)) / 2 + geom.norm(geom.newell(b)) / 2, 12.0)
        self.assertEqual(SL.split(quad, 1, 5.0)[1], None)
        pieces = SL.cut(quad, {0: [1.0, 2.0], 1: [1.5]})
        self.assertEqual(len(pieces), 6)
        self.assertEqual(SL.cut(quad, {}), [quad])

    def test_deterministic(self):
        for cell, cb in self.cbs.items():
            again = self.b.build(cell)
            self.assertEqual(cb.digest(), again.digest(), cell)
            self.assertEqual([m.colors for m in cb.meshes.values()], [m.colors for m in again.meshes.values()], cell)


class D1ShadeTests(unittest.TestCase):
    """COLOR_0 is the baked grey shade x on every kit mesh."""

    @classmethod
    def setUpClass(cls):
        cls.b = builder()
        cls.cb = cls.b.build("gf_corridor_west")

    def test_every_kit_mesh_carries_a_grey_shade(self):
        rep = LN.Report(self.cb.cell)
        LN.check_shade(self.cb, rep)
        self.assertEqual(rep.errors, [])
        meshes = list(self.cb.meshes.values()) + [m for m, _, _ in self.cb.lod_meshes.values()]
        for md in meshes:
            if md.material.startswith("castle_"):
                self.assertIsNotNone(md.colors, md.name)
                for cols in md.colors:
                    for r, g, b in cols:
                        self.assertTrue(r == g == b, md.name)
            else:
                self.assertIsNone(md.colors, md.name)

    def test_lint_flags_a_tinted_shade(self):
        cb = self.b.build("gf_corridor_west")
        md = next(m for m in cb.meshes.values() if m.material.startswith("castle_wall"))
        md.colors[0] = [(1.0, 0.9, 0.9)] * len(md.colors[0])
        rep = LN.Report(cb.cell)
        LN.check_shade(cb, rep)
        self.assertTrue(any("not a grey shade byte" in e for e in rep.errors), rep.errors)

    def test_gradient(self):
        lo_hi = (0.0, 9.0)
        self.assertEqual(SH.gradient("wall", 0.0, lo_hi), G.SHADE_WALL_BOTTOM)
        self.assertEqual(SH.gradient("wall", 9.0, lo_hi), 1.0)
        self.assertEqual(SH.gradient("trim", 0.0, lo_hi), 0.86)
        self.assertEqual(SH.gradient(None, 0.0, lo_hi), 1.0)
        self.assertEqual(SH.gradient("-", 0.0, lo_hi), 1.0)
        mid = SH.gradient("wall", 4.5, lo_hi)
        self.assertTrue(G.SHADE_WALL_BOTTOM < mid < 1.0)

    def test_participants(self):
        self.assertFalse(SH.receives("castle_glass"))
        self.assertFalse(SH.receives("castle_sky"))
        self.assertFalse(SH.receives("wall"))
        self.assertTrue(SH.receives("castle_props"))
        glow = A.WFace([], "castle_props", "kit", "flame:#FFFFFF", [], "props:x")
        self.assertTrue(SH.is_glow(glow))
        self.assertFalse(SH.occludes(glow))
        cloth = A.WFace([], "castle_cloth", "trim", "x", [], "banner")
        self.assertFalse(SH.occludes(cloth))
        lod1 = A.WFace([], "castle_trim", "trim", "x", [], "a", lod="lod1")
        self.assertFalse(SH.occludes(lod1))

    def test_weld_and_smooth(self):
        import numpy as np

        pts = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
        nrm = np.array([[0.0, 1.0, 0.0], [0.0, 0.966, 0.259], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
        ids = SH.weld(pts, nrm)
        self.assertEqual(ids[0], ids[1])  # 15 degrees apart: one cluster
        self.assertNotEqual(ids[0], ids[2])  # 90 degrees: a crease, kept apart
        v = SH.smooth(np.array([0.0, 1.0, 1.0]), np.array([[0, 1], [1, 2]]), 1, 0.5)
        self.assertEqual([float(x) for x in v], [0.5, 0.75, 1.0])  # halfway to the neighbours' mean
        fixed = SH.smooth(np.array([0.0, 1.0, 1.0]), np.array([[0, 1], [1, 2]]), 1, 0.5, np.array([False, True, False]))
        self.assertEqual([float(x) for x in fixed], [0.5, 1.0, 1.0])  # a fixed cluster keeps its value, feeds its neighbours

    def test_long_edge_samples(self):
        """Review fix: a vertex with a long edge samples along it (hat
        weights), so one dark point (a door head's moulding corner) does not
        spread up a 6 m edge; short edges add nothing."""
        import numpy as np

        class M:
            material = "castle_wall_a"
            verts = [(0.0, 0.0, 0.0), (0.2, 0.0, 0.0), (0.2, 6.0, 0.0), (0.0, 6.0, 0.0)]
            faces = [[0, 1, 2, 3]]
            src_faces = [A.WFace([], "castle_wall_a", "wall", "world", [], "a")]

        index = {(0, i): i for i in range(4)}
        P = np.array(M.verts)
        N = np.tile([0.0, 0.0, 1.0], (4, 1))
        sp, sn, sk, sw = SH.edge_samples([M], index, P, N)
        # Two 6 m edges, 0.5, 1 and 2 m from each end (3 m is half the edge): 4 x 3 samples.
        self.assertEqual(len(sp), 12)
        self.assertEqual(sorted(set(int(k) for k in sk)), [0, 1, 2, 3])
        self.assertTrue(np.allclose(sorted(set(np.round(sw, 6))), [1 - 2 / 6, 1 - 1 / 6, 1 - 0.5 / 6]))
        self.assertTrue(np.all(sp[:, 1] > 0.0) and np.all(sp[:, 1] < 6.0))
        self.assertTrue(np.allclose(sn, [0.0, 0.0, 1.0]))

    @unittest.skipUnless(SH.available(), "needs Blender (the ray cast)")
    def test_ray_cast_occlusion(self):
        """An open floor is unoccluded; a vertex in a 90-degree concave corner
        about half; the vertical wall far from anything open."""
        import numpy as np

        floor = [(-5.0, 0.0, -5.0), (-5.0, 0.0, 5.0), (5.0, 0.0, 5.0), (5.0, 0.0, -5.0)]
        wall = [(-5.0, 0.0, -5.0), (5.0, 0.0, -5.0), (5.0, 6.0, -5.0), (-5.0, 6.0, -5.0)]
        c = SH.Caster([floor, wall])
        try:
            pts = np.array([[0.0, 0.0, 3.0], [0.0, 0.0, -5.0], [0.0, 3.0, -5.0]])
            nrm = np.array([[0.0, 1.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
            ao = SH.occlusion(c, pts, nrm, 2.0, 64)
            again = SH.occlusion(c, pts, nrm, 2.0, 64)
        finally:
            c.close()
        self.assertEqual(list(ao), list(again))  # deterministic
        self.assertAlmostEqual(ao[0], 1.0, places=6)
        self.assertTrue(0.35 < ao[1] < 0.65, ao[1])
        self.assertAlmostEqual(ao[2], 1.0, places=6)

    @unittest.skipUnless(SH.available(), "needs Blender (the ray cast)")
    def test_bake_is_deterministic_and_darkens_corners(self):
        b = builder()
        b.cast_ao = True
        try:
            one, two = b.build("l7_corridor_north"), b.build("l7_corridor_north")
        finally:
            b.cast_ao = False
        self.assertTrue(one.shade_stats.baked)
        self.assertEqual(one.shade_stats.rays, G.ao_rays("corridor"))
        self.assertEqual([m.colors for m in one.meshes.values()], [m.colors for m in two.meshes.values()])
        rep = LN.Report(one.cell)
        LN.check_shade(one, rep)
        self.assertEqual(rep.errors, [])
        xs = [r for m in one.meshes.values() if m.material.startswith("castle_") for cols in m.colors for r, _, _ in cols]
        self.assertLess(min(xs), 0.6)  # corners fall into the shade
        self.assertEqual(max(xs), 1.0)  # open surfaces stay unshaded
        self.assertGreaterEqual(min(xs), G.shade_byte(G.SHADE_MIN) / 255.0)


class D1ExportTests(unittest.TestCase):
    """The export's cell files: every COLOR_0 byte grey."""

    WORLD = os.path.join(REPO, "client", "public", "assets", "world")

    def test_compare_strips_colour_only(self):
        a = [({"COLOR_0": b"\x01", "POSITION": b"p"}, b"i", "m")]
        b = [({"COLOR_0": b"\x02", "POSITION": b"p"}, b"i", "m")]
        self.assertEqual(LN._strip_colour(a), LN._strip_colour(b))
        self.assertNotEqual(LN._strip_colour(a), LN._strip_colour([({"POSITION": b"q"}, b"i", "m")]))

    def test_export_colour0_is_grey(self):
        trim = os.path.join(self.WORLD, "gf_corridor_west.glb")
        if not os.path.exists(trim):
            self.skipTest("no export")
        doc, _ = LN._glb(trim)
        prims = [p for m in doc["meshes"] for p in m["primitives"]
                 if "material" in p and doc["materials"][p["material"]]["name"] == "castle_trim"]
        if not any("COLOR_0" in p["attributes"] for p in prims):
            self.skipTest("the export predates castle D1 (run prepare.mjs)")
        problems, seen = LN.colour0_files(self.WORLD)
        self.assertEqual(problems, [])
        self.assertIn("castle_trim", seen)


# =====================================================================================
# Castle M5a (Corridors): docs/art/castle-kit.md (the corridor
# pieces, their seven cells and the Grand Staircase art pass).
# =====================================================================================

M5A_CORRIDORS = tuple(c for c, owner in G.M5A_CELLS.items() if owner == "corridors")


def _bounds(faces):
    pts = [p for f in faces for p in f.pts]
    return [min(p[k] for p in pts) for k in range(3)], [max(p[k] for p in pts) for k in range(3)]


class M5aCorridorsPieceTests(unittest.TestCase):
    """The new pieces of 23.4: sizes, triangle caps, the niche and wall rules
    each keeps by itself, palette-named colours only."""

    def test_catalogue_and_caps(self):
        caps = {G.CLASS_DOOR: G.CLASS_DOOR_TRIS, "kit_statue_robed_a": G.STATUE_TRIS, "kit_statue_robed_b": G.STATUE_TRIS,
                "kit_tapestry_2.5x4": G.TAPESTRY_TRIS, "kit_tapestry_6x3": G.TAPESTRY_TRIS, G.ROSE_WINDOW: G.ROSE_TRIS,
                G.WELL: G.WELL_TRIS}
        for name, cap in caps.items():
            self.assertIn(name, P.CATALOGUE, name)
            p = P.build(name, **P.CATALOGUE[name])
            self.assertLessEqual(p.triangles(), cap, name)
            self.assertGreater(p.triangles(), 0, name)
            for f in p.faces:
                ft = G.parse_face_tag(f.tag)
                if ft.material == "castle_props":
                    # Tinted tiles name a palette colour; only untinted tiles (the well's dark shaft) go bare.
                    tile = ft.arg.split(":")[0]
                    self.assertEqual(":@" in ft.arg, kit.TILES[tile][2], (name, f.tag))

    def test_statues_fit_their_niche(self):
        niche = G.OPENING_PIECES["kit_niche_2x3.5"]
        clear = G.STATUE_NICHE_CLEAR
        for name in G.STATUE_PIECES:
            p = P.build(name)
            lo, hi = _bounds(p.faces)
            self.assertTrue(G.STATUE_HEIGHT[0] <= hi[1] <= G.STATUE_HEIGHT[1], (name, hi[1]))
            self.assertGreaterEqual(lo[1], 0.0)
            # The origin is 0.35 m into the niche (0.7 deep): back clear, at most INTRUSION_MAX proud.
            self.assertGreaterEqual(lo[2] - 0.35, -niche.depth + clear - 1e-9, name)
            self.assertLessEqual(hi[2] - 0.35, G.INTRUSION_MAX + 1e-9, name)
            for f in p.faces:
                for x, y, _ in f.pts:
                    self.assertLessEqual(abs(x), niche.width / 2 - clear + 1e-9, name)
                    head = G.arch_height(niche.width, niche.height, min(abs(x) + clear, niche.width / 2))
                    self.assertLessEqual(y, head - clear + 1e-9, (name, x, y))
        self.assertNotEqual(P.build("kit_statue_robed_a").triangles(), P.build("kit_statue_robed_b").triangles())

    def test_tapestries(self):
        sheet = json.load(open(TEXTURES_PATH, encoding="utf-8"))["sheets"]["tapestries"]
        for name, tiles in G.TAPESTRY_PIECES.items():
            for tile in tiles:
                p = P.build(name, tile=float(tile.rsplit("_", 1)[1]))
                lo, hi = _bounds(p.faces)
                self.assertLessEqual(hi[2], G.TAPESTRY_PROUD + 1e-9, name)
                self.assertGreaterEqual(lo[2], 0.0, name)
                self.assertGreaterEqual(lo[1], 0.0, name)  # the origin is the cloth's bottom
                cloth = [f for f in p.faces if f.tag.startswith("castle_tapestry|")]
                self.assertTrue(cloth)
                self.assertEqual({f.tag for f in cloth}, {f"castle_tapestry|fit:{tile}"})
                self.assertIn(tile, sheet["tiles"])
                clo, chi = _bounds(cloth)
                _, size = name.rsplit("_", 1)
                w, h = (float(v) for v in size.split("x"))
                self.assertAlmostEqual(chi[0] - clo[0], w)
                self.assertAlmostEqual(chi[1] - clo[1], h)
            with self.assertRaises(ValueError):
                P.build(name, tile=9.0)

    def test_rose_window(self):
        p = P.build(G.ROSE_WINDOW, depth=0.8)
        lo, hi = _bounds(p.faces)
        self.assertAlmostEqual(lo[2], -0.8)
        glass = [f for f in p.faces if f.tag.startswith("castle_glass")]
        self.assertEqual(len(glass), 2)  # one round light, both sides
        r, cy = p.meta["rose"]
        self.assertEqual((r, cy), (3.0, 3.0))
        for f in glass:
            for x, y, _ in f.pts:
                self.assertAlmostEqual(math.hypot(x, y - cy), r, places=6)
        # No two coplanar faces of the tracery overlap (the lint's z-fighting rule).
        groups = {}
        for f in p.faces:
            key, n = LN._plane_key(f.pts)
            if key is not None:
                groups.setdefault(key, []).append(LN._flat2d(f.pts, n))
        for items in groups.values():
            for i, a in enumerate(items):
                for b in items[i + 1:]:
                    self.assertFalse(L._sat_overlap(a, b))
        self.assertEqual(G.OPENING_PIECES[G.ROSE_WINDOW].kind, "window")
        # Review of castle M5a: the moulded ring's foot (below the sill, the light
        # touches it) stays behind the string course whose top is at the sill:
        # wherever it is below the sill, the course's profile stands prouder.
        from castle_kit.pieces import mouldings

        prof = mouldings.STRING_PROFILE

        def course_proud(y):  # y below the sill (negative): the profile's projection there
            h = y + G.STRING.height
            for (z0, h0), (z1, h1) in zip(prof, prof[1:]):
                if h0 - 1e-9 <= h <= h1 + 1e-9:
                    return z0 if h1 == h0 else z0 + (z1 - z0) * (h - h0) / (h1 - h0)
            return 0.0

        ring = [v for f in p.faces if f.tag.startswith("castle_trim") and min(u[2] for u in f.pts) >= -1e-6
                for v in f.pts]
        below = [v for v in ring if v[1] < -1e-6]
        self.assertTrue(below)
        for x, y, z in below:
            self.assertGreaterEqual(y, -G.STRING.height)
            if y < -0.05:  # the course's rounded top: there the ring sits on it
                self.assertLessEqual(z, course_proud(y) + 1e-6, (x, y, z))

    def test_well(self):
        p = P.build(G.WELL)
        self.assertTrue(p.walkthrough)
        self.assertLessEqual(p.meta["radius"], G.WELL_RADIUS)
        self.assertGreaterEqual(p.meta["crossbar"], G.INTRUSION_HEIGHT)
        kerb = [f for f in p.faces if "@beast_stone" in f.tag or "@compass_dark" in f.tag]
        self.assertLessEqual(_bounds(kerb)[1][1], 0.9 + 1e-9)

    def test_class_door(self):
        p = P.build(G.CLASS_DOOR)
        blind = P.build("kit_door_blind_3x4.5")
        self.assertEqual(p.meta["depth"], G.OPENING_PIECES[G.CLASS_DOOR].depth)
        x0, x1, y0, y1 = p.meta["plaque"]
        self.assertAlmostEqual(x1 - x0, G.CLASS_DOOR_PLAQUE[0])
        self.assertAlmostEqual(y1 - y0, G.CLASS_DOOR_PLAQUE[1])
        self.assertAlmostEqual(y0, p.meta["apex"] + 0.15)
        self.assertGreater(p.triangles(), blind.triangles())
        tags = {f.tag for f in p.faces}
        self.assertIn(G.palette_tag("metal", "brass"), tags)
        self.assertIn("castle_wood|trim:door_planks", tags)


class M5aCorridorsCellTests(unittest.TestCase):
    """The seven Corridors cells built: lint clean, within the corridor
    budget, the heroes and openings of 23.3, the replaced greybox gone, the
    corner arches' quoins, the Portrait Gallery clear of its shafts."""

    @classmethod
    def setUpClass(cls):
        cls.b = builder()
        cls.cbs = {c: cls.b.build(c) for c in M5A_CORRIDORS}
        cls.reps = {c: LN.lint_cell(cb, cls.b) for c, cb in cls.cbs.items()}

    def test_lint_is_clean(self):
        for c, rep in self.reps.items():
            self.assertEqual(rep.errors, [], c)
            st = rep.stats
            for k in ("coverage_missing", "zfight", "degenerate", "uv_bad", "off_wall", "window_depth_bad"):
                self.assertEqual(st[k], 0, (c, k))
            self.assertLessEqual(st["intrusion_max"], G.INTRUSION_MAX + 1e-9, c)
            self.assertGreaterEqual(st["overhead_min"], G.OVERHEAD_CLEARANCE, c)
            self.assertLessEqual(st["collider_tris"], G.COLLIDER_TRIS, c)
            self.assertLessEqual(st["loops"]["added"], st["loops"]["allowance"], c)

    def test_budgets(self):
        b = G.BUDGETS["corridor"]
        for c, cb in self.cbs.items():
            self.assertEqual(cb.plan.budget, "corridor", c)
            self.assertLessEqual(cb.draws(), b.draws, c)
            self.assertLessEqual(cb.triangles(), b.tris[1], c)
            self.assertGreaterEqual(cb.triangles(), 0.8 * b.tris[0], c)  # (l4, l5 a little under: a warning)
            for name in cb.meshes:
                self.assertTrue(name.startswith(f"{c}.castle_"), name)
        self.assertGreaterEqual(self.cbs["gf_undercroft"].triangles(), G.M5A_TRIS["gf_undercroft"][0])
        self.assertGreaterEqual(self.cbs["ff_gallery_east"].triangles(), G.M5A_TRIS["ff_gallery_east"][0])

    def test_materials_tell_the_floors_apart(self):
        mats = {c: set(cb.library()) for c, cb in self.cbs.items()}
        self.assertIn("castle_tapestry", mats["l5_corridor_west"])
        self.assertEqual({c for c, m in mats.items() if "castle_tapestry" in m}, {"l5_corridor_west"})
        self.assertIn("castle_portraits_b", mats["l4_corridor_west"])
        for c, m in mats.items():
            self.assertIn("castle_wall_a", m, c)
            self.assertIn("castle_floor_square", m, c)
        pieces = {c: {u.piece for u in cb.uses} for c, cb in self.cbs.items()}
        self.assertIn(G.CLASS_DOOR, pieces["tf_corridor_west"])
        self.assertIn("kit_armour_suit", pieces["l6_corridor_west"])
        self.assertIn(G.ROSE_WINDOW, pieces["ff_gallery_east"])
        self.assertIn(G.WELL, pieces["gf_undercroft"])
        for c in ("gf_undercroft", "ff_gallery_east"):
            self.assertTrue(set(G.STATUE_PIECES) <= pieces[c], c)
        self.assertEqual(sum(1 for u in self.cbs["l6_corridor_west"].uses if u.piece == "kit_armour_suit"), 6)
        self.assertEqual(sum(1 for u in self.cbs["gf_undercroft"].uses if u.piece in G.STATUE_PIECES), 5)
        self.assertEqual(sum(1 for u in self.cbs["ff_gallery_east"].uses if u.piece in G.STATUE_PIECES), 4)

    def test_statues_stand_in_niches(self):
        for c in ("gf_undercroft", "ff_gallery_east"):
            cb = self.cbs[c]
            niches = [o for o in cb.plan.openings if o.kind == "niche"]
            for h in cb.plan.hero:
                if h.piece not in G.STATUE_PIECES:
                    continue
                n = next((o for o in niches if o.t0 < h.pos[0] < o.t1 and o.axis == "z"), None)
                self.assertIsNotNone(n, (c, h.id))
                self.assertAlmostEqual(h.pos[0], n.centre, places=3)
                self.assertAlmostEqual(h.pos[1], n.y0, places=6)
                self.assertAlmostEqual((h.pos[2] - n.c) * n.sign, -0.35, places=6)

    def test_replaced_greybox_and_the_charms_door(self):
        tf = self.cbs["tf_corridor_west"]
        self.assertFalse(any(f.src == "props:charms_door" for f in tf.faces))
        door = next(o for o in tf.plan.openings if o.piece == G.CLASS_DOOR)
        self.assertAlmostEqual(door.centre, -80.0)
        for c, cb in self.cbs.items():
            srcs = {f.src for f in cb.faces}
            for r in cb.plan.replaces:
                self.assertNotIn(f"props:{r}", srcs, (c, r))

    def test_corner_arches_drop_their_corner_quoins(self):
        for lv in (4, 5, 6):
            cb = self.cbs[f"l{lv}_corridor_west"]
            arch = next(u for u in cb.uses if u.id == f"arch:l{lv}_vestibule:w:arch:wt_door_l{lv}")
            self.assertIs(arch.kwargs()["quoins"], False)
            q = [u for u in cb.uses if u.id.startswith(f"quoin:l{lv}_vestibule:w:arch:wt_door_l{lv}")]
            self.assertEqual(len(q), 1)  # the free jamb (z 0) keeps its quoins
            self.assertAlmostEqual(q[0].origin[2], 0.0)
        for c, cb in self.cbs.items():
            for u in cb.uses:
                if u.role == "arch" and "wt_door" not in u.id and ":arch:sp_" not in u.id:  # (passage doors plain)
                    self.assertNotIn("quoins", u.kwargs(), (c, u.id))

    def test_portrait_gallery_keeps_clear_of_its_shafts(self):
        from castle_kit.pieces import frames as FR

        cb = self.cbs["l4_corridor_west"]
        ctx = R.context(self.b.layout, cb.plan, self.b.kit_entries, self.b.open_edges)
        laid = R.m5a_portrait_frames(ctx)
        runs = {r.id: r for r in cb.plan.runs}
        self.assertTrue(cb.plan.shafts)
        for pc in cb.plan.portraits:
            keep = R.m5a_portrait_keep_out(ctx, pc.axis, pc.c, pc.sign)
            spans = FR.free_spans(pc.t0, pc.t1, pc.y0, pc.y1, keep)
            for sh in cb.plan.shafts:
                r = runs[sh.run]
                if r.axis == pc.axis and abs(r.c - pc.c) < 1e-6 and r.sign == pc.sign and pc.t0 < sh.t < pc.t1:
                    self.assertFalse(any(a < sh.t < b for a, b in spans), (pc.id, sh.id))
            frames = laid[pc.id]
            self.assertTrue(frames, pc.id)
            free = sum(b - a for a, b in spans) * (pc.y1 - pc.y0)
            cover = sum(f[2] * f[3] for f in frames) / free
            # The densely hung walls (PORTRAIT_FILL) over the wall the shafts leave free.
            self.assertTrue(G.PORTRAIT_FILL[0] <= cover <= G.PORTRAIT_FILL[1], (pc.id, cover))
            for ta, yb, w, h, _, _ in frames:
                for k in keep:
                    self.assertFalse(ta < k[1] and k[0] < ta + w and yb < k[3] and k[2] < yb + h, pc.id)
        # The built cell hangs exactly these frames.
        uses = {u.id: u for u in cb.uses if u.role == "portraits"}
        for pc in cb.plan.portraits:
            self.assertEqual(len(uses[f"portraits:{pc.id}"].kwargs()["frames"]), len(laid[pc.id]))

    def test_deterministic(self):
        again = A.Builder.from_files()
        for c in ("l4_corridor_west", "ff_gallery_east"):
            self.assertEqual(again.build(c).digest(), self.cbs[c].digest(), c)
            self.assertEqual(R.to_json(R.placements(R.context(again.layout, again.kit_plan.cells[c], again.kit_entries,
                                                              again.open_edges))), R.to_json(self.cbs[c].uses))


class M5aCorridorsStaircaseTests(unittest.TestCase):
    """The Grand Staircase art pass: castle_wall_c on both cells, the
    denser portrait fill, still within 22 draws and the 180 k drawn estimate."""

    def test_wall_c_and_fill(self):
        b = builder()
        from castle_kit.pieces import frames as FR

        for c in ("grand_staircase", "grand_staircase_upper"):
            cp = b.kit_plan.cells[c]
            self.assertEqual(cp.materials["castle_wall"], "castle_wall_c")
            ctx = R.context(b.layout, cp, b.kit_entries, b.open_edges)
            laid = R.portrait_frames(ctx)
            for pc in cp.portraits:
                cov = FR.coverage(laid[pc.id], pc.t0, pc.t1, pc.y0, pc.y1)
                self.assertTrue(G.PORTRAIT_FILL_M5A[0] <= cov <= G.PORTRAIT_FILL_M5A[1], (pc.id, cov))
        self.assertEqual(G.PORTRAIT_FILL, G.PORTRAIT_FILL_M5A)
        # No other kitted cell uses the darker stone.
        for c, cp in b.kit_plan.cells.items():
            if c not in ("grand_staircase", "grand_staircase_upper"):
                self.assertNotEqual(cp.materials["castle_wall"], "castle_wall_c", c)


# =====================================================================================
# Castle M5a (Tower): the west tower's spiral stair (docs/art/castle-kit.md "The kitted cells", "Pieces").
# =====================================================================================

TOWER = "west_tower_stair"


class M5aTowerPieceTests(unittest.TestCase):
    """The tower's three pieces: within their triangle caps, the hard rules
    each keeps by itself, colours from palette names only."""

    @classmethod
    def setUpClass(cls):
        b = builder()
        cp = b.kit_plan.cells[TOWER]
        core = next(h for h in cp.hero if h.piece == G.SPIRAL_CORE)
        cls.runs = R.core_runs(b.layout, cp, core)
        cls.core = P.build(G.SPIRAL_CORE, top=dict(core.args)["top"], base=core.pos[1], runs=cls.runs)
        cls.core_pos = core.pos
        cls.slit = P.build(G.SLIT_WINDOW, depth=G.EXTERIOR_SHELL)
        cls.door = P.build(G.KNOCKER_DOOR)

    def test_triangle_caps(self):
        self.assertLessEqual(self.core.triangles(), G.SPIRAL_CORE_TRIS)
        self.assertLessEqual(self.slit.triangles(), G.SLIT_TRIS)
        self.assertLessEqual(self.door.triangles(), G.KNOCKER_DOOR_TRIS)
        for name in (G.SPIRAL_CORE, G.SLIT_WINDOW, G.KNOCKER_DOOR):
            self.assertIn(name, P.CATALOGUE)

    def test_core_runs_follow_every_flight_and_floor_on_the_core(self):
        # 12 flights (3 a face, 4 on the east face less its foot landing) plus the
        # foot landing (east) and the top landing (north).
        self.assertEqual(len(self.runs), 14)
        sides = sorted(r[0] for r in self.runs)
        self.assertEqual(sides, sorted(["n"] * 4 + ["e"] * 4 + ["s"] * 3 + ["w"] * 3))
        for side, u0, u1, y0, y1 in self.runs:
            self.assertAlmostEqual(u1 - u0, 4.0)
            self.assertIn(round(abs(y1 - y0), 6), (0.0, 2.25))
        self.assertEqual(self.core.meta["arches"], 2 * len(self.runs))

    def test_core_relief_and_shell(self):
        """Below its cornice nothing stands more than CORE_RELIEF off the
        core's faces (within the intrusion limit and the camera's wall
        relief), and the collider shell encloses it."""
        from castle_kit.pieces import tower

        half = 2.0
        under = self.core.meta["height"] - G.CORNICE.height  # (the cornice ring under the ceiling: the corridors' 0.35 m)
        out = max(max(abs(pt[0]), abs(pt[2])) - half for f in self.core.faces for pt in f.pts if pt[1] < under - 1e-6)
        self.assertLessEqual(out, tower.CORE_RELIEF + 1e-9)
        self.assertLessEqual(tower.CORE_RELIEF, G.INTRUSION_MAX)
        shell = self.core.meta["collider"]
        self.assertEqual(len(shell), 4)
        r = max(abs(c) for q in shell for pt in q for c in (pt[0], pt[2]))
        self.assertGreater(r, half + tower.CORE_RELIEF)
        top = max(pt[1] for f in self.core.faces for pt in f.pts)
        self.assertAlmostEqual(top, 60.75 - self.core_pos[1])
        self.assertAlmostEqual(max(pt[1] for q in shell for pt in q), top)

    def test_slit_window_closes_the_hole(self):
        # The glazed light lies inside the 0.6 x 2.4 opening, between the faces.
        glass = [f for f in self.slit.faces if f.tag.startswith("castle_glass")]
        self.assertEqual(len(glass), 2)
        for f in glass:
            for x, y, z in f.pts:
                self.assertLess(abs(x), 0.3)
                self.assertTrue(0.0 < y < 2.4)
                self.assertTrue(-G.EXTERIOR_SHELL < z < 0.0)
        # Its inside frame stays within the intrusion limit.
        self.assertLessEqual(max(pt[2] for f in self.slit.faces for pt in f.pts), G.INTRUSION_MAX)
        # The outer face exists only with `outer`.
        outer = P.build(G.SLIT_WINDOW, depth=G.EXTERIOR_SHELL, outer=False)
        self.assertLess(outer.triangles(), self.slit.triangles())
        self.assertFalse(any(abs(pt[2] + G.EXTERIOR_SHELL) < 1e-9 and f.normal()[2] < -0.99
                             for f in outer.faces for pt in f.pts))

    def test_knocker_door_stays_in_the_passage(self):
        self.assertTrue(self.door.walkthrough)
        self.assertLess(max(pt[2] for f in self.door.faces for pt in f.pts), 0.0)  # behind the wall face
        tags = {f.tag for f in self.door.faces}
        self.assertIn(G.palette_tag("metal", "bronze"), tags)
        self.assertIn("castle_wood|trim:door_planks", tags)
        # The leaves fill the 4 x 4.5 arch; the knocker hangs at hand height.
        w, h = 4.0, 4.5
        xs = [pt[0] for f in self.door.faces for pt in f.pts]
        ys = [pt[1] for f in self.door.faces for pt in f.pts]
        self.assertAlmostEqual(max(xs) - min(xs), w)
        self.assertAlmostEqual(max(ys), h)
        bronze = [pt[1] for f in self.door.faces if f.tag == G.palette_tag("metal", "bronze") for pt in f.pts]
        self.assertTrue(0.9 < min(bronze) and max(bronze) < 2.0)

    def test_bronze_is_a_palette_slot(self):
        textures = S.load_texture_set(TEXTURES_PATH)
        self.assertIn("bronze", textures["palette"])
        self.assertIn(["metal", "@bronze"], textures["props_slots"])


class M5aTowerTests(unittest.TestCase):
    """The west tower built: lint clean within the tower budget, the stepped
    flights round the arcaded core, the slit windows with their outer faces,
    the knocker door in common room C's arch, the shaft's cornice, the upper
    landings' plinths, borders and corbels, placements byte-identical."""

    @classmethod
    def setUpClass(cls):
        cls.b = builder()
        cls.cb = cls.b.build(TOWER)
        cls.rep = LN.lint_cell(cls.cb, cls.b)

    def uses(self, role):
        return [u for u in self.cb.uses if u.role == role]

    def test_lint_is_clean_within_the_tower_budget(self):
        self.assertEqual(self.rep.errors, [])
        st = self.rep.stats
        b = G.BUDGETS["tower"]
        self.assertLessEqual(st["draws"], b.draws)
        self.assertLessEqual(st["tris"], b.tris[1])
        self.assertLessEqual(st["collider_tris"], G.COLLIDER_TRIS)
        self.assertLessEqual(st["intrusion_max"], G.INTRUSION_MAX + 1e-9)
        self.assertGreaterEqual(st["overhead_min"], G.OVERHEAD_CLEARANCE)
        self.assertLessEqual(st["tread_error_max"], G.TREAD_RAMP_TOLERANCE)
        for k in ("coverage_missing", "zfight", "degenerate", "uv_bad", "off_wall", "window_depth_bad"):
            self.assertEqual(st[k], 0, k)
        self.assertEqual(st["doors_checked"], 5)
        self.assertLessEqual(st["loops"]["added"], G.slice_allowance("tower", st["loops"]["unsliced"]))
        self.assertLessEqual(st["loops"]["added"], G.SLICE_ADD_MAX["tower"])
        self.assertEqual(sorted(self.cb.library()), ["castle_ceiling", "castle_floor_square", "castle_glass", "castle_props",
                                                     "castle_trim", "castle_wall_a", "castle_wood"])

    def test_stepped_flights_round_the_core(self):
        stairs = sorted(s.id for s in self.b.layout.surfaces if s.cell == TOWER and s.kind == "stair")
        flights = self.uses("flight")
        self.assertEqual(sorted(u.run for u in flights), stairs)
        self.assertEqual(len(flights), 12)
        for u in flights:
            self.assertEqual(u.piece, "kit_stair_flight_stepped")
            p = self.cb.pieces[u.id]
            self.assertEqual(p.meta["steps"], 13)
            self.assertTrue(p.meta["stepped"])

    def test_arcaded_core_replaces_the_cap(self):
        core = next(u for u in self.uses("hero") if u.piece == G.SPIRAL_CORE)
        self.assertEqual(core.origin, (-100.0, 27.0, 2.0))
        kw = core.kwargs()
        self.assertEqual((kw["base"], kw["top"]), (27.0, 60.75))
        self.assertEqual(len(kw["runs"]), 14)
        shell = self.cb.pieces[core.id].meta["collider"]
        pl = geom.Placement(core.origin, core.yaw)
        world = [[pl.point(p) for p in q] for q in shell]
        self.assertIn(sorted(map(tuple, world[0])), [sorted(map(tuple, c)) for c in self.cb.collider])
        self.assertIn("wt_core_cap", self.cb.plan.replaces)

    def test_slit_windows_have_their_outer_faces(self):
        slits = [u for u in self.uses("window") if u.piece == G.SLIT_WINDOW]
        self.assertEqual(len(slits), 6)
        self.assertTrue(all("outer" not in u.kwargs() for u in slits))
        patches = R.tower_shell_patches(self.b.layout, self.cb.plan)
        # The north wall under common room C: the Planner draws no outer face there.
        self.assertEqual(len(patches), 1)
        axis, c, sign, ta, tb, y0, y1, plane, holes = patches[0]
        self.assertEqual((axis, c, sign, plane), ("z", -4.0, 1.0, -4.8))
        self.assertEqual(len(holes), 2)
        self.assertEqual(y1, 54.0 - L.SLAB)
        self.assertLessEqual(y0, 27.0)
        self.assertNotIn("string", {u.role for u in self.cb.uses})

    def test_corner_arches_have_no_quoins(self):
        arches = {u.id.rsplit(":", 1)[1]: u.kwargs() for u in self.uses("arch")}
        self.assertEqual(sorted(arches), ["cr_c_door", "wt_door", "wt_door_l4", "wt_door_l5", "wt_door_l6"])
        for door in ("wt_door", "wt_door_l4", "wt_door_l5", "wt_door_l6"):
            self.assertIs(arches[door].get("quoins"), False, door)
        self.assertNotIn("quoins", arches["cr_c_door"])

    def test_knocker_door_in_common_room_cs_arch(self):
        door = next(u for u in self.uses("hero") if u.piece == G.KNOCKER_DOOR)
        arch = next(o for o in self.cb.plan.openings if o.door == "cr_c_door")
        self.assertAlmostEqual(door.origin[0], (arch.t0 + arch.t1) / 2)
        self.assertAlmostEqual(door.origin[2], arch.c)
        self.assertAlmostEqual(door.origin[1], arch.y0)
        for gone in ("cr_c_door_leaf", "cr_c_knocker", "wt_torch_0", "wt_torch_1", "wt_torch_2"):
            self.assertIn(gone, self.cb.plan.replaces)
        self.assertFalse(any(f.src.startswith("props:cr_c_") for f in self.cb.faces))
        lights = [lt for lt in L.load_lights(PROPS_PATH) if lt.cell == TOWER]
        self.assertEqual(len(lights), 3)  # the greybox torches' lights stay (the manifest's, beside the lanterns)

    def test_shaft_cornice_plinths_borders_and_corbels(self):
        cornices = self.uses("cornice")
        self.assertEqual(len(cornices), 4)
        for u in cornices:
            kw = u.kwargs()
            self.assertEqual(kw["ends"], ["miter", "miter"])
            self.assertAlmostEqual(kw["length"], 12.0)
            self.assertAlmostEqual(u.origin[1], 60.75)
        landings = [s for s in self.b.layout.surfaces if s.cell == TOWER and s.kind == "corridor"]
        bordered = {u.id.split(":", 1)[1] for u in self.uses("floor_border")}
        self.assertEqual(bordered, {s.id for s in landings if s.id != "wt_e0"} | {"wt_e0"})
        corbels = self.uses("corbel")
        upper = {s.id for s in landings if s.y > 36 - 1e-6}
        self.assertEqual({u.id.split(":")[1] for u in corbels}, upper)
        lowest = {"wt_e0", "wt_ne0", "wt_nw0", "wt_sw0", "wt_se0"}
        self.assertFalse(lowest & {u.id.split(":")[1] for u in corbels})

    def test_placements_byte_identical(self):
        ctx = R.context(self.b.layout, self.cb.plan, self.b.kit_entries, self.b.open_edges)
        self.assertEqual(R.to_json(R.placements(ctx)), R.to_json(self.cb.uses))
        self.assertEqual(self.b.build(TOWER).digest(), self.cb.digest())



# =====================================================================================
# Castle M5b (Furniture): docs/art/castle-kit.md "Pieces".
# =====================================================================================

FURNITURE_SCENE = os.path.join(HERE, "data", "m5b_furniture_scene.json")
FURNITURE_CAPS = {"kit_sofa": G.SOFA_TRIS, "kit_armchair": G.ARMCHAIR_TRIS, "kit_rug": G.RUG_TRIS,
                  "kit_table_round": G.TABLE_ROUND_TRIS, "kit_table_study": G.TABLE_STUDY_TRIS,
                  "kit_chair_study": G.CHAIR_STUDY_TRIS, "kit_noticeboard": G.NOTICEBOARD_TRIS, "kit_door_dorm": G.DORM_DOOR_TRIS,
                  "kit_chandelier_ring": G.CHANDELIER_TRIS}


def _furniture_catalogue():
    return {n: prm for n, prm in P.CATALOGUE.items() if P.parse(n)[0] in G.FURNITURE_PIECES}


def _palette_names(piece):
    """The palette names of a piece's props-atlas faces (`kit:<tile>:@<name>`)."""
    out = set()
    for f in piece.faces:
        ft = G.parse_face_tag(f.tag)
        if ft.material == "castle_props" and ft.arg and ":@" in ft.arg:
            out.add(ft.arg.split(":@", 1)[1])
    return out


class M5bFurniturePieceTests(unittest.TestCase):
    """The room furniture set: names, caps, seats and their knee room,
    floor seats, the wall pieces' depth, colours by palette name, determinism."""

    def test_catalogue_covers_the_set(self):
        fams = {P.parse(n)[0] for n in _furniture_catalogue()}
        self.assertEqual(fams, set(G.FURNITURE_PIECES))
        for name, prm in _furniture_catalogue().items():
            p = P.build(name, **prm)
            self.assertTrue(G.PIECE_RE.match(name), name)
            self.assertLessEqual(p.triangles(), FURNITURE_CAPS[P.parse(name)[0]], name)
            self.assertGreater(p.triangles(), 0, name)

    def test_caps_at_the_largest_sizes(self):
        self.assertLessEqual(P.build("kit_sofa_3.2", cover=4).triangles(), G.SOFA_TRIS)
        self.assertLessEqual(P.build("kit_rug_8x8", field=3).triangles(), G.RUG_TRIS)
        self.assertLessEqual(P.build("kit_table_study", length=3.2).triangles(), G.TABLE_STUDY_TRIS)
        self.assertLessEqual(P.build("kit_door_dorm", house=2, round=1).triangles(), G.DORM_DOOR_TRIS)
        big = P.build("kit_chandelier_ring", radius=G.CHANDELIER_RADIUS[1], count=G.CHANDELIER_COUNT[1], chain=8.0, group=0)
        self.assertLessEqual(big.triangles(), G.CHANDELIER_TRIS)
        for bad in (lambda: P.build("kit_sofa_1.2"), lambda: P.build("kit_rug_9x2"),
                    lambda: P.build("kit_chandelier_ring", count=30), lambda: P.build("kit_door_dorm", house=4),
                    lambda: P.build("kit_armchair", cover=6)):
            with self.assertRaises(ValueError):
                bad()

    def test_seat_rows_say_how_far_the_seat_reaches(self):
        """Seats fit the sitter's legs: every seat piece's row says its front
        edge (`front` ahead of the row's centre line), and nothing of the
        piece under its top and over the floor its feet rest on (`drop`
        below the top) reaches further ahead within the row's width; its
        back (`back`) is the backrest's face or the seat's back edge."""
        expect = {"kit_sofa_2.4": (0.40, 0.25, G.SEAT_TOP), "kit_armchair": (0.40, 0.25, G.SEAT_TOP),
                  "kit_chair_study": (0.24, 0.19, G.SEAT_TOP), "kit_bench_13": (0.2, 0.2, 0.45),
                  "kit_chair_row_staff": (0.24, 0.19, 0.52), "kit_chair_high": (0.24, 0.19, 0.56),
                  G.FOUNTAIN: (0.26, 0.22, round(G.SEAT_TOP - 0.12, 4))}
        for name, fit in expect.items():
            p = P.build(name, **P.CATALOGUE.get(name, {}))
            rows = p.meta["seats"]
            self.assertTrue(rows, name)
            for x0, z0, x1, z1, y, yaw, front, back, drop in rows:
                self.assertEqual((front, back, drop), fit, name)
                cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
                c, s = math.cos(yaw), math.sin(yaw)
                # The row's frame: +z the way the sitter faces, x across.
                along = abs(x1 - x0) if abs(c) > 0.5 else abs(z1 - z0)
                for f in p.faces:
                    pts = [((q[0] - cx) * c - (q[2] - cz) * s, q[1], (q[0] - cx) * s + (q[2] - cz) * c) for q in f.pts]
                    lo = [min(q[k] for q in pts) for k in range(3)]
                    hi = [max(q[k] for q in pts) for k in range(3)]
                    if hi[0] <= -along / 2 + 1e-3 or lo[0] >= along / 2 - 1e-3:
                        continue  # beside the row (a sofa's arms, the next chair)
                    if lo[1] >= y - 1e-3 or hi[1] <= y - drop + 0.02:
                        continue  # above the top, or the floor the feet rest on
                    if hi[2] <= -back - 0.3:
                        continue
                    self.assertLessEqual(hi[2], front + 1e-3, (name, f.pts))

    def test_seats_fit_the_seated_clip(self):
        for name, prm in [("kit_sofa_1.6", {}), ("kit_sofa_2.4", {}), ("kit_sofa_3.2", {}), ("kit_armchair", {}),
                          ("kit_chair_study", {})]:
            p = P.build(name, **prm)
            rows = p.meta["seats"]
            self.assertEqual(len(rows), 1, name)
            x0, z0, x1, z1, y, yaw, front, back, drop = rows[0]
            self.assertEqual((y, yaw, drop), (G.SEAT_TOP, 0.0, G.SEAT_TOP), name)
            self.assertTrue(0.0 < front <= G.SEAT_FRONT_MAX and 0.0 < back, name)
            self.assertTrue(G.SEAT_ROW_DEPTH[0] <= z1 - z0 <= G.SEAT_ROW_DEPTH[1], name)
            self.assertGreaterEqual(x1 - x0, G.SEAT_WIDTH_MIN, name)
            cz = (z0 + z1) / 2
            # The seat's top surface: the highest faces over the row, at SEAT_TOP, ending SEAT_FRONT_MAX ahead.
            tops = [f for f in p.faces if all(abs(q[1] - G.SEAT_TOP) < 1e-6 for q in f.pts)
                    and all(x0 - 0.05 <= q[0] <= x1 + 0.05 for q in f.pts)]
            self.assertTrue(tops, name)
            self.assertLessEqual(max(q[2] for f in tops for q in f.pts), cz + G.SEAT_FRONT_MAX + 1e-6, name)
            self.assertLessEqual(max(q[2] for f in tops for q in f.pts), cz + front + 1e-6, name)
            # Knee room: none of the piece itself in the box ahead of the row.
            for f in p.faces:
                lo = [min(q[k] for q in f.pts) for k in range(3)]
                hi = [max(q[k] for q in f.pts) for k in range(3)]
                inside = (hi[0] > x0 + G.KNEE_CLEAR_INSET + 1e-3 and lo[0] < x1 - G.KNEE_CLEAR_INSET - 1e-3
                          and hi[1] > 0.03 + 1e-3 and lo[1] < G.KNEE_CLEAR_TOP - 1e-3
                          and hi[2] > cz + G.SEAT_FRONT_MAX + 1e-3 and lo[2] < cz + G.KNEE_CLEAR_AHEAD - 1e-3)
                self.assertFalse(inside, (name, f.pts))
        for name in ("kit_sofa_2.4", "kit_armchair"):
            self.assertGreaterEqual(P.build(name).meta["arm_gap"], G.ARM_GAP_MIN, name)
        self.assertEqual(P.build("kit_sofa_2.4").meta["sitters"], 3)
        self.assertEqual(P.build("kit_sofa_1.6").meta["sitters"], 2)

    def test_rug_floor_seat(self):
        p = P.build("kit_rug_4x3", field=1)
        self.assertEqual(p.meta["floor_seats"], [[-1.9, -1.4, 1.9, 1.4, G.RUG_THICK]])
        self.assertAlmostEqual(max(q[1] for f in p.faces for q in f.pts), G.RUG_THICK)
        self.assertAlmostEqual(min(q[1] for f in p.faces for q in f.pts), 0.0)
        self.assertFalse(any(f.normal()[1] < -0.5 for f in p.faces))  # no underside on the floor (no z-fight)
        self.assertTrue(p.walkthrough)
        self.assertEqual(_palette_names(p), {"house_b", "rug_border"})
        self.assertEqual(_palette_names(P.build("kit_rug_2x2", field=4)), {"rug_neutral", "rug_border"})

    def test_house_colours_only_by_house_names(self):
        for i, name in enumerate(G.FURNITURE_COVERS):
            self.assertIn(name, _palette_names(P.build("kit_sofa_2.4", cover=i)))
            self.assertIn(name, _palette_names(P.build("kit_armchair", cover=i)))
        for h in range(4):
            self.assertIn(f"house_{'abcd'[h]}", _palette_names(P.build("kit_door_dorm", house=h)))
        palette = PA.palette(TEXTURES_PATH)
        slots = {(t, c[1:]) for t, c in PA.extra_slots(TEXTURES_PATH) if c.startswith("@")}
        for name, prm in _furniture_catalogue().items():
            p = P.build(name, **prm)
            for f in p.faces:
                ft = G.parse_face_tag(f.tag)
                self.assertNotRegex(f.tag, r"#[0-9A-Fa-f]{6}", name)
                if ft.material == "castle_props" and ft.arg and ":@" in ft.arg:
                    tile, colour = ft.arg.split(":@", 1)
                    self.assertIn(colour, palette, name)
                    self.assertIn((tile, colour), slots, (name, tile, colour))
        for name in ("upholstery_leather", "rug_neutral", "rug_border", "cork"):
            self.assertIn(name, palette)

    def test_wall_pieces(self):
        for name, prm in (("kit_noticeboard", {}), ("kit_door_dorm", {"house": 0}), ("kit_door_dorm", {"house": 1, "round": 1})):
            p = P.build(name, **prm)
            self.assertFalse(p.walkthrough, name)
            zs = [q[2] for f in p.faces for q in f.pts]
            self.assertGreaterEqual(min(zs), -1e-9, name)
            self.assertLessEqual(max(zs), min(0.15, G.INTRUSION_MAX) + 1e-9, name)  # at most 0.15 m off the wall
            self.assertIn("wall", p.meta, name)
        board = P.build("kit_noticeboard")
        self.assertAlmostEqual(min(q[1] for f in board.faces for q in f.pts), 1.1 - 0.08)
        door = P.build("kit_door_dorm")
        self.assertAlmostEqual(min(q[1] for f in door.faces for q in f.pts), 0.0)
        self.assertGreaterEqual(max(q[1] for f in door.faces for q in f.pts), 2.6)

    def test_chandelier(self):
        p = P.build("kit_chandelier_ring", radius=1.0, count=12, chain=6.0, group=1, amp=0.03)
        ys = [q[1] for f in p.faces for q in f.pts]
        self.assertAlmostEqual(min(ys), -G.CHANDELIER_BELOW)
        self.assertAlmostEqual(max(ys), 6.0)
        # Animated fire: each candle's flame is the client's (meta "candles": x, y, z, height), on its wick.
        self.assertEqual(len(p.meta["candles"]), 12)
        self.assertFalse([f for f in p.faces if f.tag == G.palette_tag("glow", "glow_candle")])
        for x, y, z, h in p.meta["candles"]:
            self.assertAlmostEqual(math.hypot(x, z), 1.0, places=3)
            self.assertGreater(y, 0.0)
            self.assertGreater(h, 0.08)
        self.assertTrue(p.walkthrough)
        self.assertIsNone(p.lod1)

    def test_deterministic(self):
        for name, prm in _furniture_catalogue().items():
            a, b = P.build(name, **prm), P.build(name, **prm)
            self.assertEqual(_h([[f.pts, f.tag, f.uv] for f in a.faces]), _h([[f.pts, f.tag, f.uv] for f in b.faces]), name)


def furniture_scene():
    """(builder, cell build, lint report) of the test scene: castle_kit.json
    with the scene's cell flipped to build, its heroes and replaces."""
    with open(FURNITURE_SCENE, encoding="utf-8") as f:
        scene = json.load(f)
    data = S.load_json(S.KIT_PATH)
    spec = data["cells"][scene["cell"]]
    spec.update(build=True, hero=scene["hero"], replaces=scene["replaces"])
    lay = L.load_layout(LAYOUT_PATH)
    plan, rep = S.resolve(data, lay, PROPS_PATH, S.load_texture_set(TEXTURES_PATH))
    assert rep.ok, rep.errors
    b = A.Builder(lay, L.load_props(PROPS_PATH), kit.load_kit(PROPS_PATH), L.load_open_edges(PROPS_PATH), plan,
                  S.load_texture_set(TEXTURES_PATH), PROPS_PATH, TEXTURES_PATH)
    cb = b.build(scene["cell"])
    return scene, b, cb, LN.lint_cell(cb, b)


class M5bFurnitureSceneTests(unittest.TestCase):
    """The set placed in common room A (tests/data/m5b_furniture_scene.json):
    lint clean (knee room, floor seats, budget, greyscale COLOR_0), the
    manifest's seats and floor seats, the chandelier's candle group, a
    byte-identical rebuild."""

    @classmethod
    def setUpClass(cls):
        cls.scene, cls.b, cls.cb, cls.rep = furniture_scene()

    def test_lint_clean(self):
        self.assertEqual(self.rep.errors, [])
        self.assertEqual(self.rep.stats["knee_boxes"], 8)
        self.assertEqual(self.rep.stats["floor_seats"], 1)
        b = G.BUDGETS["common_room"]
        self.assertLessEqual(self.cb.draws(), b.draws)

    def test_seats_and_floor_seats(self):
        self.assertEqual(len(self.cb.seats), 8)  # two sofas, two armchairs, four study chairs: one row each
        for x0, z0, x1, z1, y, yaw, *_fit in self.cb.seats:
            self.assertAlmostEqual(y, 63 + G.SEAT_TOP)
            self.assertTrue(x0 < x1 and z0 < z1)
        self.assertEqual(self.cb.floor_seats, [[-56.9, -42.9, -53.1, -38.1, 63.02]])  # the 5 x 4 rug turned along z

    def test_chandelier_group(self):
        name = "common_room_house_a.castle_props.candles_0"
        self.assertIn(name, self.cb.meshes)
        self.assertEqual(self.cb.extras[name][G.EXTRAS_ANIM]["kind"], "bob")
        self.assertNotIn(G.lod1_name(name), self.cb.lod_meshes)
        lowest = min(v[1] for v in self.cb.meshes[name].verts)
        self.assertAlmostEqual(lowest, 69 - G.CHANDELIER_BELOW)
        self.assertGreaterEqual(lowest - 63 - G.CHANDELIER_BOB_AMP, G.OVERHEAD_CLEARANCE)

    def test_knee_room_detects(self):
        # A study table pushed against the leather sofa's front: lint names the sofa's knee box.
        data = S.load_json(S.KIT_PATH)
        spec = data["cells"][self.scene["cell"]]
        hero = [dict(h) for h in self.scene["hero"]] + [
            {"id": "bad_table", "piece": "kit_table_study", "pos": [-44, 63, -39.6], "yaw": 0.0}]
        spec.update(build=True, hero=hero, replaces=self.scene["replaces"])
        plan, rep = S.resolve(data, self.b.layout, PROPS_PATH, S.load_texture_set(TEXTURES_PATH))
        self.assertTrue(rep.ok, rep.errors)
        b = A.Builder(self.b.layout, self.b.props, self.b.kit_entries, self.b.open_edges, plan, self.b.textures, PROPS_PATH,
                      TEXTURES_PATH)
        lrep = LN.lint_cell(b.build(self.scene["cell"]), b)
        self.assertTrue(any("knee room" in e and "sofa_leather" in e for e in lrep.errors), lrep.errors)

    def test_wall_piece_off_shafts_detects(self):
        # The noticeboard moved onto the south wall's shaft at x -42.5: lint names it.
        data = S.load_json(S.KIT_PATH)
        spec = data["cells"][self.scene["cell"]]
        hero = [dict(h, pos=[-42.5, 63, -30]) if h["id"] == "noticeboard" else dict(h) for h in self.scene["hero"]]
        spec.update(build=True, hero=hero, replaces=self.scene["replaces"])
        plan, rep = S.resolve(data, self.b.layout, PROPS_PATH, S.load_texture_set(TEXTURES_PATH))
        self.assertTrue(rep.ok, rep.errors)
        b = A.Builder(self.b.layout, self.b.props, self.b.kit_entries, self.b.open_edges, plan, self.b.textures, PROPS_PATH,
                      TEXTURES_PATH)
        lrep = LN.lint_cell(b.build(self.scene["cell"]), b)
        self.assertTrue(any("noticeboard" in e and "shaft" in e for e in lrep.errors), lrep.errors)

    def test_rebuild_identical(self):
        self.assertEqual(self.b.build(self.scene["cell"]).digest(), self.cb.digest())


# =====================================================================================
# Castle M5b (Dungeons): docs/art/castle-kit.md "The kitted cells", "Pieces" (the dungeon pieces and
# their seven cells).
# =====================================================================================

DUNGEON_CELLS = tuple(c for c, owner in G.M5B_CELLS.items() if owner == "dungeons")
DUNGEON_CAPS = {"kit_torch_wall": G.TORCH_WALL_TRIS, "kit_door_stone_hidden": G.STONE_DOOR_TRIS,
                "kit_barrel_stack": G.BARREL_STACK_TRIS, "kit_hearth_kitchen": G.KITCHEN_HEARTH_TRIS,
                "kit_pans_hanging": G.PANS_HANGING_TRIS, "kit_pots_set": G.POTS_SET_TRIS,
                # The basement rooms' shared small pieces (pieces/dungeon.py docstrings).
                "kit_lamp_hanging": 300, "kit_window_glow": 400, "kit_plant_pot": 300, "kit_shelf_wall": 1000,
                "kit_door_painting": 100, "kit_stair_skirt": 200}
DUNGEON_MODULE_PIECES = (*DUNGEON_CAPS, G.VAULT_BARREL)


def _dungeon_catalogue():
    return {n: prm for n, prm in P.CATALOGUE.items() if P.parse(n)[0] in DUNGEON_MODULE_PIECES}


class M5bDungeonsPieceTests(unittest.TestCase):
    """The dungeon pieces: names, caps, colours by palette name, the
    vault's section and ribs, the torch over 2.4 m, the hidden door behind the
    wall face, the barrel stack, the hearth's floor seat, the hanging pieces'
    lowest points, the still-life mix, determinism."""

    def test_catalogue_and_caps(self):
        fams = {P.parse(n)[0] for n in _dungeon_catalogue()}
        self.assertEqual(fams, set(DUNGEON_MODULE_PIECES))
        self.assertTrue(set(G.DUNGEON_PIECES) <= fams)
        for name, prm in _dungeon_catalogue().items():
            p = P.build(name, **prm)
            self.assertTrue(G.PIECE_RE.match(name), name)
            # Vegetation look A: a tree or hedge piece is plantings of the foliage kit, no faces of its own.
            self.assertGreater(p.triangles() + len(p.meta.get("foliage", [])), 0, name)
            fam, w, h, _ = P.parse(name)
            cap = G.VAULT_BARREL_TRIS_PER_M * h if fam == G.VAULT_BARREL else DUNGEON_CAPS[fam]
            self.assertLessEqual(p.triangles(), cap, name)
        for name, prm in (("kit_vault_barrel_21x21", {"rise": 3.0, "rib0": -6.56, "pitch": 6.23, "ribs": 3}),
                          ("kit_vault_barrel_6x20", {"rise": 3.0, "axis": 1, "rib0": -3.5, "pitch": 7, "ribs": 2})):
            fam, w, h, _ = P.parse(name)
            self.assertLessEqual(P.build(name, **prm).triangles(), G.VAULT_BARREL_TRIS_PER_M * h, name)
        self.assertLessEqual(P.build("kit_hearth_kitchen", width=4.0, hood=1, flue=8.4).triangles(), G.KITCHEN_HEARTH_TRIS)
        self.assertLessEqual(P.build("kit_pans_hanging", length=4.0, count=12).triangles(), G.PANS_HANGING_TRIS)
        for bad in (lambda: P.build("kit_vault_barrel_6x20", rise=3.5), lambda: P.build("kit_torch_wall", height=2.4),
                    lambda: P.build("kit_barrel_stack", rows=6), lambda: P.build("kit_pots_set", count=6),
                    lambda: P.build("kit_door_painting", canvas=12), lambda: P.build("kit_vault_barrel_9x28", ribs=2, rib0=-14)):
            with self.assertRaises(ValueError):
                bad()

    def test_colours_by_palette_name_only(self):
        palette = PA.palette(TEXTURES_PATH)
        slots = {(t, c[1:]) for t, c in PA.extra_slots(TEXTURES_PATH) if c.startswith("@")}
        variants = [("kit_hearth_kitchen", {"hood": 1, "flue": 8.0}), ("kit_lamp_hanging", {"glow": 0}),
                    ("kit_window_glow", {"round": 1, "glow": 1})]
        for name, prm in [*_dungeon_catalogue().items(), *variants]:
            p = P.build(name, **prm)
            for f in p.faces:
                self.assertNotRegex(f.tag, r"#[0-9A-Fa-f]{6}", name)
                ft = G.parse_face_tag(f.tag)
                if ft.material == "castle_props" and ft.arg and ":@" in ft.arg:
                    tile, colour = ft.arg.split(":@", 1)
                    self.assertIn(colour, palette, name)
                    self.assertIn((tile, colour), slots, (name, tile, colour))
        for name in ("copper", "glow_lake", "glow_green", "glow_sun", "plant"):
            self.assertIn(name, palette)
        self.assertIn("copper", _palette_names(P.build("kit_hearth_kitchen", hood=1)))
        self.assertNotIn("copper", _palette_names(P.build("kit_hearth_kitchen", hood=0)))
        # Common room D's lanterns glow candle-warm (its green stays in the fabrics).
        self.assertIn("glow_lantern", _palette_names(P.build("kit_lamp_hanging", glow=1)))
        self.assertNotIn("glow_green", _palette_names(P.build("kit_lamp_hanging", glow=1)))
        self.assertIn("glow_lake", _palette_names(P.build("kit_window_glow", glow=0)))
        self.assertIn("glow_sun", _palette_names(P.build("kit_window_glow", glow=1, round=1)))
        # Animated fire: the torch's flame is the client's (meta "fires"), its pitch head soot.
        self.assertEqual(len(P.build("kit_torch_wall").meta["fires"]), 1)

    def test_barrel_vault(self):
        p = P.build("kit_vault_barrel_9x28", rise=3.0, rib0=-8.5, pitch=8.5, ribs=3, cap=1, cap_x0=-2.5, cap_x1=1.5)
        webs = [f for f in p.faces if f.tag == "castle_ceiling|world"]
        ribs = [f for f in p.faces if f.tag == "castle_trim|trim:rib"]
        self.assertTrue(webs and ribs)
        ys = [q[1] for f in webs for q in f.pts]
        self.assertAlmostEqual(max(ys), 3.0, places=6)  # the crown
        self.assertAlmostEqual(min(ys), 0.0, places=6)  # the springing at both walls
        self.assertAlmostEqual(max(abs(q[0]) for f in webs for q in f.pts), 4.5, places=6)
        for f in webs:  # the web faces into the vault (down and in)
            n = f.normal()
            c = [sum(q[k] for q in f.pts) / len(f.pts) for k in range(3)]
            self.assertLess(n[0] * c[0] + n[1] * (c[1] - p.meta["rise"] + p.meta["radius"]), 0.0)
        self.assertEqual(p.meta["ribs"], [-8.5, 0.0, 8.5])
        zs = {round(q[2], 6) for f in ribs for q in f.pts}
        self.assertTrue(all(any(abs(z - r) <= 0.15 for r in p.meta["ribs"]) for z in zs))
        caps = [f for f in p.faces if f.tag == "castle_wall|world"]
        self.assertTrue(caps)
        self.assertTrue(all(abs(q[2] + 14.0) < 1e-9 and -2.5 - 1e-9 <= q[0] <= 1.5 + 1e-9 for f in caps for q in f.pts))
        self.assertEqual({round(f.normal()[2]) for f in caps}, {-1, 1})  # both faces
        self.assertTrue(p.meta["collider"])
        # axis 1 turns it to run along local x.
        q = P.build("kit_vault_barrel_6x20", rise=3.0, axis=1, rib0=-3.5, pitch=7.0, ribs=2)
        pts = [v for f in q.faces for v in f.pts]
        self.assertAlmostEqual(max(abs(v[0]) for v in pts), 10.0, places=6)
        self.assertLessEqual(max(abs(v[2]) for v in pts), 3.0 + 0.2)

    def test_torch_over_head_height(self):
        for h in (2.6, 2.8, 3.2):
            p = P.build("kit_torch_wall", height=h)
            pts = [q for f in p.faces for q in f.pts]
            # Below 2.4 m (a 2.6 m bracket) at most INTRUSION_MAX proud; from 2.8 m all over it.
            self.assertTrue(all(q[2] <= G.INTRUSION_MAX + 1e-9 for q in pts if q[1] < G.OVERHEAD_CLEARANCE), h)
            if h >= 2.8:
                self.assertGreaterEqual(min(q[1] for q in pts), G.OVERHEAD_CLEARANCE)
            self.assertGreaterEqual(min(q[2] for q in pts), -1e-9)
            self.assertLessEqual(max(q[2] for q in pts), 0.45)
            self.assertNotIn("lights", p.meta)
        lit = P.build("kit_torch_wall", light=1)
        self.assertEqual(len(lit.meta["lights"]), 1)
        self.assertEqual(lit.meta["lights"][0]["glow"], "fire")

    def test_hidden_door_is_wall_stone_behind_the_face(self):
        p = P.build("kit_door_stone_hidden")
        self.assertTrue(p.walkthrough)
        self.assertEqual({f.tag for f in p.faces}, {"castle_wall|world"})
        zs = {round(q[2], 6) for f in p.faces for q in f.pts}
        self.assertTrue(all(z < 0 for z in zs))  # never on the wall's plane
        pts = [q for f in p.faces for q in f.pts]
        self.assertAlmostEqual(max(q[1] for q in pts), 4.5)
        self.assertAlmostEqual(max(abs(q[0]) for q in pts), 2.0)
        self.assertEqual({round(f.normal()[2]) for f in p.faces}, {-1, 1})

    def test_barrel_stack(self):
        p = P.build("kit_barrel_stack")
        self.assertTrue(p.walkthrough)
        self.assertEqual(p.meta["barrels"], 20)
        pts = [q for f in p.faces for q in f.pts]
        self.assertGreaterEqual(min(q[2] for q in pts), 0.02 - 1e-9)  # off the wall's plane
        self.assertGreaterEqual(min(q[1] for q in pts), 0.0)
        self.assertGreater(max(q[1] for q in pts), 3.5)  # it fills the 3.5 m opening
        self.assertGreater(max(q[0] for q in pts), 1.75)
        # Portrait back: soot is the backing panel seen in the gaps between the barrels.
        self.assertEqual(_palette_names(p), {"oak", "oak_aged", "iron", "soot"})

    def test_hearth(self):
        p = P.build("kit_hearth_kitchen", width=4.0, top=6.0, flue=8.4)
        self.assertTrue(p.walkthrough)
        (x0, z0, x1, z1, y), = p.meta["floor_seats"]
        self.assertGreaterEqual(min(x1 - x0, z1 - z0), G.FLOOR_SEAT_MIN)
        self.assertLessEqual(y, G.FLOOR_SEAT_HEIGHT_MAX)
        tops = [f for f in p.faces if f.tag == "castle_floor|world"]
        self.assertTrue(any(all(abs(q[1] - y) < 1e-9 for q in f.pts) for f in tops))
        self.assertEqual(p.meta["wall"][3], 8.4)
        self.assertTrue(p.meta["collider"])
        self.assertEqual(len(p.meta["lights"]), 1)
        self.assertNotIn("lights", P.build("kit_hearth_kitchen", light=0).meta)
        pts = [q for f in p.faces for q in f.pts]
        self.assertGreaterEqual(min(q[2] for q in pts), -1e-9)  # nothing behind the wall face

    def test_hanging_pieces_lowest_points(self):
        for name, prm in (("kit_pans_hanging", {"drop": 2.2}), ("kit_lamp_hanging", {"drop": 6.6, "glow": 1}),
                          ("kit_lamp_hanging", {"drop": 0.5, "glow": 0})):
            p = P.build(name, **prm)
            low = min(q[1] for f in p.faces for q in f.pts)
            self.assertAlmostEqual(low, p.meta["lowest"], delta=0.03)
            self.assertLessEqual(max(q[1] for f in p.faces for q in f.pts), 1e-6)  # nothing over the hook

    def test_wall_pieces(self):
        for name, prm in (("kit_window_glow", {}), ("kit_window_glow", {"round": 1, "w": 1.2}), ("kit_shelf_wall", {})):
            p = P.build(name, **prm)
            self.assertIn("wall", p.meta, name)
            self.assertGreaterEqual(min(q[2] for f in p.faces for q in f.pts), -1e-9, name)
        self.assertFalse(P.build("kit_window_glow").walkthrough)
        self.assertLessEqual(max(q[2] for f in P.build("kit_window_glow").faces for q in f.pts), G.INTRUSION_MAX)

    def test_still_life_mix_hangs_still_lifes(self):
        from castle_kit.pieces import frames as FR

        self.assertIsNone(FR.mix_kind(G.PORTRAIT_FRAMES))
        self.assertEqual(FR.mix_kind(G.PORTRAIT_FRAMES_STILL_LIFE), "still_life")
        lo, hi = G.PORTRAIT_KINDS["still_life"]
        laid = FR.layout_cluster(0.0, 8.0, 1.0, 3.6, 4801, G.PORTRAIT_FRAMES_STILL_LIFE, (), "castle_portraits_a")
        self.assertTrue(laid)
        self.assertTrue(all(lo <= c < hi for *_, c in laid))
        gallery = FR.layout_cluster(0.0, 8.0, 1.0, 3.6, 4801, G.PORTRAIT_FRAMES, (), "castle_portraits_a")
        self.assertFalse(all(lo <= c < hi for *_, c in gallery))  # the gallery still mixes its kinds

    def test_deterministic(self):
        for name, prm in _dungeon_catalogue().items():
            a, b = P.build(name, **prm), P.build(name, **prm)
            self.assertEqual(_h([[f.pts, f.tag, f.uv] for f in a.faces]), _h([[f.pts, f.tag, f.uv] for f in b.faces]), name)


class M5bDungeonsCellTests(unittest.TestCase):
    """The seven cells as kitted: lint clean (draws, the kind's triangle
    cap, collider, greyscale COLOR_0, loops, coverage, intrusion, door
    clearance, knee room, the hanging pieces), no greybox material left, the
    cauldrons kept, the vaults on their springing, the solid basement steps,
    the plain and flush arches, seats and floor seats, a byte-identical
    rebuild."""

    @classmethod
    def setUpClass(cls):
        cls.b = builder()
        cls.cbs = {c: cls.b.build(c) for c in DUNGEON_CELLS}
        cls.reps = {c: LN.lint_cell(cb, cls.b) for c, cb in cls.cbs.items()}

    def test_built_and_lint_clean(self):
        for c in DUNGEON_CELLS:
            self.assertIn(c, self.b.kit_plan.cells, c)
            rep, cb = self.reps[c], self.cbs[c]
            self.assertEqual(rep.errors, [], c)
            budget = G.BUDGETS[G.M5B_BUDGET[c]]
            self.assertLessEqual(cb.draws(), budget.draws, c)
            self.assertLessEqual(cb.triangles(), budget.tris[1], c)
            self.assertLessEqual(cb.triangles(), G.M5B_TRIS[c][1], c)
            self.assertLessEqual(cb.collider_triangles(), G.COLLIDER_TRIS, c)
            self.assertTrue(all(m.material.startswith("castle_") for m in cb.meshes.values()), (c, sorted(cb.meshes)))
            # Pieces bring lights only within kit.LIGHTS_PER_CELL (the Potions kit alone already has 11).
            kit_lights = len(self.b.cell_kits[c].lights) if c in self.b.cell_kits else 0
            self.assertLessEqual(len(cb.lights), max(kit.LIGHTS_PER_CELL, kit_lights), c)
        self.assertLessEqual(self.cbs["dungeon_classroom"].triangles(), 70_000)

    def test_cauldrons_and_kit_kept(self):
        cp = self.b.kit_plan.cells["dungeon_classroom"]
        srcs = {f.src for f in self.cbs["dungeon_classroom"].faces}
        for kid in G.M5B_KEEP_KIT:
            self.assertNotIn(kid, cp.replaces)
            self.assertIn(f"props:{kid}", srcs)
        for kid in ("pc_shelves_w", "pc_blackboard", "pc_teacher_desk", "tell_potions_shelf"):
            self.assertIn(f"props:{kid}", srcs)
        self.assertNotIn("props:pc_floor", srcs)
        for c, tell in (("dungeon_west", "tell_dungeon_alcove"), ("dungeon_west", "tell_dungeon_corridor_end"),
                        ("basement_east", "tell_kitchen_portrait_nook"), ("common_room_house_b", "tell_cr_b_burrow"),
                        ("common_room_house_d", "tell_cr_d_lake_window")):
            self.assertIn(f"props:{tell}", {f.src for f in self.cbs[c].faces}, (c, tell))

    def test_vaults(self):
        for c in ("dungeon_west", "scullery_passage", "dungeon_classroom"):
            cb = self.cbs[c]
            self.assertEqual(cb.plan.springing, 5.4, c)
            vault = [u for u in cb.uses if u.piece.startswith(G.VAULT_BARREL + "_")]
            self.assertEqual(len(vault), 1, c)
            floor = min(s.y for s in self.b.layout.surfaces if s.cell == c and s.kind != "door" and not s.sloped)
            self.assertAlmostEqual(vault[0].origin[1], floor + 5.4, msg=c)
            # No flat Planner ceiling over the springing (the vault is the ceiling).
            flat = [f for f in cb.faces if f.src == "base" and f.material == "castle_ceiling"
                    and min(q[1] for q in f.pts) > floor + 5.4 + 1e-6 and min(q[1] for q in f.pts) < floor + 8.5]
            self.assertEqual(flat, [], c)
            # Every rib foot stands on a shaft or a corbel.
            piece = cb.pieces[vault[0].id]
            feet = 0
            for z in piece.meta["ribs"]:
                for x in (-piece.meta["span"] / 2, piece.meta["span"] / 2):
                    lx, lz = (x, z) if piece.meta["axis"] == 0 else (z, -x)
                    wx, wz = vault[0].origin[0] + lx, vault[0].origin[2] + lz
                    near = [u for u in cb.uses if u.role in ("shaft", "corbel")
                            and math.hypot(u.origin[0] - wx, u.origin[2] - wz) < 0.7]
                    kit_near = [k for k in self.b.kit_entries if k.cell == c and k.model == "stone_column"
                                and math.hypot(k.pos[0] - wx, k.pos[2] - wz) < 1.0]
                    self.assertTrue(near or kit_near, (c, x, z))
                    feet += 1
            self.assertGreaterEqual(feet, 4, c)

    def test_steps_solid_and_arches(self):
        for c in ("dungeon_west", "basement_east", "common_room_house_d"):
            flights = [u for u in self.cbs[c].uses if u.role == "flight"]
            self.assertTrue(flights, c)
            self.assertTrue(all(u.kwargs()["solid"] for u in flights), c)
            skirts = [u for u in self.cbs[c].uses if u.role == "skirt"]
            self.assertEqual(sorted(u.run for u in skirts), sorted(u.run for u in flights), c)
            # Review fixes: stone treads, never the Marble Staircase's strip their tread matches.
            self.assertTrue(all("strip" not in u.kwargs() for u in flights), c)
        for c, door, rim in (("dungeon_west", "cr_d_wall", False), ("basement_east", "kitchen_painting_door", False),
                             ("common_room_house_d", "cr_d_wall", True)):
            u = next(u for u in self.cbs[c].uses if u.role == "arch" and u.id.endswith(":arch:" + door))
            kw = u.kwargs()
            self.assertIs(kw["quoins"], False, (c, door))
            self.assertEqual(kw.get("rim", True), rim, (c, door))
        u = next(u for u in self.cbs["dungeon_west"].uses if u.role == "arch" and u.id.endswith(":arch:dungeon_classroom_door"))
        self.assertNotIn("quoins", u.kwargs())  # the other arches as before

    def test_hidden_door_and_painting_cover_their_doors(self):
        door = self.b.layout.by_id("cr_d_wall")
        u = next(u for u in self.cbs["dungeon_west"].uses if u.piece == G.STONE_DOOR)
        self.assertAlmostEqual(u.origin[0], (door.x0 + door.x1) / 2)
        self.assertAlmostEqual(u.origin[2], door.z0)
        self.assertIn("cr_d_wall_panel", self.cbs["dungeon_west"].plan.replaces)
        door = self.b.layout.by_id("kitchen_painting_door")
        u = next(u for u in self.cbs["basement_east"].uses if u.piece == "kit_door_painting")
        self.assertAlmostEqual(u.origin[0], (door.x0 + door.x1) / 2)
        self.assertGreaterEqual(self.cbs["basement_east"].pieces[u.id].meta["width"], door.x1 - door.x0)
        stack = next(u for u in self.cbs["basement_east"].uses if u.piece == G.BARREL_STACK)
        door = self.b.layout.by_id("cr_b_barrels")
        self.assertAlmostEqual(stack.origin[2], (door.z0 + door.z1) / 2)
        self.assertAlmostEqual(stack.origin[0], door.x0)

    def test_web_hood(self):
        # Review fixes: common room D's hood in the vault web's stone, so it reads on the dark wall.
        cb = self.cbs["common_room_house_d"]
        (fire,) = [u for u in cb.uses if u.role == "fireplace"]
        self.assertEqual(fire.kwargs().get("hood"), "web")
        self.assertTrue(any(f.tag.startswith("castle_ceiling|") for f in cb.pieces[fire.id].faces))
        self.assertIn("castle_ceiling", {m.material for m in cb.meshes.values()})

    def test_beamed_ceiling(self):
        uses = self.cbs["common_room_house_b"].uses
        self.assertFalse([u for u in uses if u.id.startswith(("rib:", "diagonal:", "boss:"))])
        self.assertEqual(sum(1 for u in uses if u.piece == "kit_beam_ridge"), 5)
        self.assertTrue([u for u in self.cbs["common_room_house_d"].uses if u.id.startswith("rib:")])  # D keeps its ribs

    def test_seats_and_floor_seats(self):
        k = self.cbs["kitchens"]
        self.assertEqual(len(k.seats), 8)  # eight benches, one row each
        # The furniture standing on a floor seat is cut out of it, so the
        # hearth step is two rows, either side of the pots on it.
        self.assertEqual(len(k.floor_seats), 2)  # the hearth step
        x0, z0, x1, z1, y = k.floor_seats[0]
        self.assertAlmostEqual(y, -9.0 + 0.3)
        self.assertEqual(len(self.cbs["dungeon_classroom"].floor_seats), 2)
        b = self.cbs["common_room_house_b"]
        self.assertEqual(len(b.seats), 14)  # two sofas, four armchairs, eight study chairs (review fixes: +6)
        self.assertEqual(len(b.floor_seats), 8)  # three rugs (the round table's in four rows), the hearth step in two
        d = self.cbs["common_room_house_d"]
        self.assertEqual(len(d.seats), 16)  # review fixes: the nook's two armchairs, the lake sofa
        self.assertEqual(len(d.floor_seats), 8)  # four rugs, the nook's round its armchairs, the round table's edge
        for cb in self.cbs.values():
            for row in cb.seats:
                self.assertTrue(row[0] < row[2] and row[1] < row[3])

    def test_house_accents(self):
        names = set()
        for u in self.cbs["common_room_house_b"].uses:
            if u.role == "hero":
                names |= _palette_names(self.cbs["common_room_house_b"].pieces[u.id])
        self.assertIn("house_b", names)
        self.assertFalse({"house_a", "house_c", "house_d"} & names)
        names = set()
        for u in self.cbs["common_room_house_d"].uses:
            if u.role == "hero":
                names |= _palette_names(self.cbs["common_room_house_d"].pieces[u.id])
        self.assertIn("house_d", names)
        self.assertFalse({"house_a", "house_b", "house_c"} & names)

    def test_rebuild_identical(self):
        for c in DUNGEON_CELLS:
            self.assertEqual(self.b.build(c).digest(), self.cbs[c].digest(), c)


# =====================================================================================
# Castle M5b (Upper rooms): docs/art/castle-kit.md "The kitted cells", "Pieces".
# =====================================================================================

M5B_UPPER = tuple(c for c, owner in G.M5B_CELLS.items() if owner == "upper_rooms")
UPPER_CAPS = {"kit_trophy_case": G.TROPHY_CASE_TRIS, "kit_armillary": G.ARMILLARY_TRIS,
              "kit_statue_scholar": G.STATUE_SCHOLAR_TRIS, "kit_lamp_reading": None, "kit_dome_starry": G.DOME_STARRY_TRIS,
              "kit_floor_inlay_map": G.FLOOR_MAP_TRIS, "kit_drapes": None}


def _upper_catalogue():
    fams = (*G.UPPER_PIECES, "kit_drapes", "kit_lamp_reading")
    return {n: prm for n, prm in P.CATALOGUE.items() if P.parse(n)[0] in fams}


def _upper_tris(p):
    """LOD0 triangles a piece adds to its cell (the dome\x27s castle_sky panels too)."""
    return p.triangles() + sum(len(pts) - 2 for pts, _ in p.meta.get("sky", []))


class M5bUpperPieceTests(unittest.TestCase):
    """The upper-room pieces: the catalogue, caps at their largest
    sizes, palette-named colours, the bookcases\x27 and cases\x27 depth and wall
    span, the armillary\x27s clearance and light, the floor map tiling its
    square, the dome\x27s sky panels, determinism."""

    def test_catalogue_covers_the_pieces_and_caps(self):
        from castle_kit.pieces import rooms_upper as RU

        fams = {P.parse(n)[0] for n in _upper_catalogue()}
        self.assertEqual(fams, {*G.UPPER_PIECES, RU.DRAPES, RU.LAMP})
        caps = dict(UPPER_CAPS, kit_lamp_reading=RU.LAMP_TRIS, kit_drapes=RU.DRAPES_TRIS)
        for name, prm in _upper_catalogue().items():
            p = P.build(name, **prm)
            fam, w, _, _ = P.parse(name)
            cap = G.BOOKCASE_TRIS_PER_M * w if fam == G.BOOKCASE else caps[fam]
            self.assertLessEqual(_upper_tris(p), cap, name)
            self.assertGreater(p.triangles(), 0, name)
            for f in p.faces:
                ft = G.parse_face_tag(f.tag)
                if ft.material == "castle_props":
                    tile = ft.arg.split(":")[0]
                    self.assertEqual(":@" in ft.arg, kit.TILES[tile][2], (name, f.tag))
        # The largest sizes.
        self.assertLessEqual(P.build("kit_bookcase_6x6", seed=3).triangles(), G.BOOKCASE_TRIS_PER_M * 6)
        self.assertLessEqual(P.build("kit_bookcase_1x2", seed=3).triangles(), G.BOOKCASE_TRIS_PER_M * 1)
        self.assertLessEqual(P.build("kit_trophy_case", width=3.0, seed=7).triangles(), G.TROPHY_CASE_TRIS)
        self.assertLessEqual(P.build("kit_floor_inlay_map_12x12").triangles(), G.FLOOR_MAP_TRIS)
        self.assertLessEqual(_upper_tris(P.build("kit_dome_starry_30", rise=6.0)), G.DOME_STARRY_TRIS)
        for bad in (lambda: P.build("kit_bookcase_7x3"), lambda: P.build("kit_trophy_case", house=4),
                    lambda: P.build("kit_armillary", centre=3.0), lambda: P.build("kit_drapes_2.6x9", house=5),
                    lambda: P.build("kit_floor_inlay_map_20x4")):
            with self.assertRaises(ValueError):
                bad()

    def test_new_palette_names_and_slots(self):
        palette = PA.palette(TEXTURES_PATH)
        slots = {(t, c[1:]) for t, c in PA.extra_slots(TEXTURES_PATH) if c.startswith("@")}
        for name in ("book_red", "book_green", "book_blue", "book_brown", "book_ochre", "map_sea", "map_green"):
            self.assertIn(name, palette)
        names = set()
        for name, prm in _upper_catalogue().items():
            names |= _palette_names(P.build(name, **prm))
            for f in P.build(name, **prm).faces:
                ft = G.parse_face_tag(f.tag)
                if ft.material == "castle_props" and ":@" in (ft.arg or ""):
                    tile, colour = ft.arg.split(":@", 1)
                    self.assertIn((tile, colour), slots, (name, tile, colour))
        self.assertTrue({"book_red", "book_blue", "map_sea", "gilt", "bronze", "house_c"} <= names, names)
        # House accents only through the house names (the trophy shields, the drapes).
        self.assertIn("house_d", _palette_names(P.build("kit_drapes_2.6x9", house=3)))
        self.assertIn("house_b", _palette_names(P.build("kit_trophy_case", house=1, seed=2)))

    def test_wall_pieces_depth_and_span(self):
        for name, prm, depth in (("kit_bookcase_5x4.2", {}, 0.5), ("kit_bookcase_2x2", {"cupboard": 0}, 0.5),
                                 ("kit_trophy_case", {"width": 2.4}, 0.6)):
            p = P.build(name, **prm)
            lo, hi = _bounds(p.faces)
            self.assertGreaterEqual(lo[2], -1e-9, name)
            self.assertLessEqual(hi[2], depth + 1e-9, name)
            self.assertTrue(p.walkthrough, name)
            x0, x1, y0, y1 = p.meta["wall"]
            self.assertLessEqual(x0, lo[0] + 1e-9, name)
            self.assertGreaterEqual(x1, hi[0] - 1e-9, name)
            self.assertAlmostEqual(y0, 0.0)
            self.assertGreaterEqual(y1, hi[1] - 1e-9, name)
        drapes = P.build("kit_drapes_2.6x9", house=2)
        lo, hi = _bounds(drapes.faces)
        self.assertFalse(drapes.walkthrough)
        self.assertLessEqual(hi[2], G.INTRUSION_MAX)  # a wall piece within the intrusion rule
        self.assertGreater(lo[1], G.PLINTH.height)  # the hem over the plinth (it stays whole)

    def test_armillary_hangs_clear_and_glows(self):
        p = P.build("kit_armillary", centre=5.2, top=8.4)
        lo, hi = _bounds(p.faces)
        self.assertGreaterEqual(lo[1], G.OVERHEAD_CLEARANCE)
        self.assertAlmostEqual(hi[1], 8.4)
        self.assertTrue(p.walkthrough)
        (light,) = p.meta["lights"]
        self.assertIn(light["glow"], __import__("palette").GLOW)
        self.assertAlmostEqual(light["pos"][1], 5.2)
        self.assertIn("glow_lantern", _palette_names(p))

    def test_floor_map_tiles_its_square(self):
        p = P.build("kit_floor_inlay_map_7x5")
        self.assertEqual(p.meta["floor_cut"], [(-3.5, -2.5, 3.5, 2.5)])
        area = 0.0
        for f in p.faces:
            self.assertTrue(all(abs(q[1]) < 1e-9 for q in f.pts))
            self.assertGreater(f.normal()[1], 0.99)
            area += f.area()
        self.assertAlmostEqual(area, 7 * 5, places=6)  # flush, no overlaps, no holes
        kinds = _palette_names(p)
        self.assertEqual(kinds, {"compass_dark", "compass_light", "map_sea", "map_green", "brass"})

    def test_dome_sky_panels(self):
        p = P.build("kit_dome_starry_24", rise=4.9)
        sky = p.meta["sky"]
        self.assertEqual(len(sky), 4 * 9 * 10 + 1)
        for pts, uv in sky:
            self.assertEqual(len(pts), len(uv))
            self.assertTrue(all(-1e-9 <= a <= 1 + 1e-9 and -1e-9 <= b <= 1 + 1e-9 for a, b in uv))
            n = geom.unit(geom.newell(pts))
            c = [sum(q[k] for q in pts) / len(pts) for k in range(3)]
            # Every panel faces into the room (towards a point under the dome's middle).
            self.assertGreater(n[0] * -c[0] + n[1] * (-2.0 - c[1]) + n[2] * -c[2], 0.0)
            self.assertTrue(all(-12 - 1e-6 <= q[0] <= 12 + 1e-6 and -1e-9 <= q[1] <= 4.9 + 1e-9 for q in pts))
        self.assertTrue(p.meta["collider"])
        self.assertLess(p.meta["boss"], p.meta["crown"])

    def test_scholar_statue(self):
        p = P.build("kit_statue_scholar")
        lo, hi = _bounds(p.faces)
        self.assertTrue(2.6 <= hi[1] <= 3.4, hi[1])
        self.assertAlmostEqual(lo[1], 0.0)
        self.assertTrue(p.walkthrough)
        self.assertTrue({"statue_stone", "beast_stone"} <= _palette_names(p))

    def test_deterministic(self):
        for name, prm in _upper_catalogue().items():
            a, b = P.build(name, **prm), P.build(name, **prm)
            self.assertEqual(_h([[f.pts, f.tag, f.uv] for f in a.faces]), _h([[f.pts, f.tag, f.uv] for f in b.faces]), name)
        self.assertNotEqual(_h([f.pts for f in P.build("kit_bookcase_3x3", seed=1).faces]),
                            _h([f.pts for f in P.build("kit_bookcase_3x3", seed=2).faces]))


class M5bUpperCellTests(unittest.TestCase):
    """The five Upper rooms cells built: lint clean within their budget
    kinds and loop allowances, D1\x27s bake on every kit mesh, the heroes the
    brief asks for, seats and floor seats, the dome\x27s castle_sky panels and
    collider, the floor map\x27s cut, the solid Trophy Stair flight, one light
    id per lamp, placements byte-identical."""

    @classmethod
    def setUpClass(cls):
        cls.b = builder()
        cls.cbs = {c: cls.b.build(c) for c in M5B_UPPER}
        cls.reps = {c: LN.lint_cell(cb, cls.b) for c, cb in cls.cbs.items()}

    def heroes(self, cell, piece):
        return [u for u in self.cbs[cell].uses if u.role == "hero" and P.parse(u.piece)[0] == piece]

    def test_built_and_lint_clean_within_budget(self):
        self.assertEqual(sorted(M5B_UPPER), ["common_room_house_a", "common_room_house_c", "sf_library", "trophy_room",
                                             "trophy_stair"])
        for cell, cb in self.cbs.items():
            rep, kind = self.reps[cell], cb.plan.budget
            self.assertEqual(rep.errors, [], cell)
            self.assertEqual(kind, G.M5B_BUDGET[cell])
            b = G.BUDGETS[kind]
            self.assertLessEqual(cb.draws(), b.draws, cell)
            self.assertLessEqual(cb.triangles(), b.tris[1], cell)
            self.assertLessEqual(cb.collider_triangles(), G.COLLIDER_TRIS, cell)
            st = rep.stats
            self.assertLessEqual(st["loops"]["added"], G.slice_allowance(kind, st["loops"]["unsliced"]), cell)
            self.assertLessEqual(st["loops"]["added"], G.SLICE_ADD_MAX[kind], cell)
            for k in ("coverage_missing", "zfight", "degenerate", "uv_bad", "off_wall", "window_depth_bad"):
                self.assertEqual(st[k], 0, (cell, k))
            meshes = list(cb.meshes.values()) + [md for md, _, _ in cb.lod_meshes.values()]  # (castle M5c: facade LOD1)
            self.assertEqual(st["shade"]["corners"], sum(len(f) for m in meshes for f in m.faces), cell)
            self.assertFalse([m for m in cb.meshes.values() if not m.material.startswith("castle_")], cell)

    def test_common_room_a(self):
        cell = "common_room_house_a"
        cb = self.cbs[cell]
        self.assertIn(f"{cell}.castle_props.candles_0", cb.meshes)
        fire = [o for o in cb.plan.openings if o.kind == "fireplace"]
        self.assertEqual(len(fire), 1)
        self.assertAlmostEqual(fire[0].centre, -40.5)  # on the greybox fire\x27s spot, the hearth find spot before it
        door = self.heroes(cell, G.DORM_DOOR)[0]
        self.assertAlmostEqual(door.origin[0], -49.5)  # on the dorm stairs\x27 interactable
        self.assertEqual(len([o for o in cb.plan.openings if o.kind == "window"]), 4)
        self.assertEqual(len(cb.seats), 9)
        self.assertEqual(len(cb.floor_seats), 7)  # Two rugs less their round tables (four rows and three)
        for kid in ("craft_charms_house_a", "craft_transfiguration_house_a", "tell_cr_a_hearth"):
            self.assertNotIn(kid, cb.plan.replaces)
            self.assertTrue(any(f.src == f"props:{kid}" for f in cb.faces), kid)
        for want in (G.SOFA, G.ARMCHAIR, G.RUG, G.TABLE_ROUND, G.TABLE_STUDY, G.CHAIR_STUDY, G.NOTICEBOARD, G.CHANDELIER,
                     G.BOOKCASE):
            self.assertTrue(self.heroes(cell, want), want)

    def test_common_room_c_dome_statue_and_bookcases(self):
        cell = "common_room_house_c"
        cb = self.cbs[cell]
        self.assertEqual(cb.plan.springing, 9.0)
        sky = cb.meshes[f"{cell}.castle_sky"]
        dome = self.heroes(cell, G.DOME_STARRY)[0]
        self.assertEqual(len(sky.faces), len(cb.pieces[dome.id].meta["sky"]))
        self.assertLessEqual(max(v[1] for v in sky.verts), 54 + 14 + 1e-6)
        self.assertGreaterEqual(min(v[1] for v in sky.verts), 54 + 9 - 1e-6)
        shell = cb.pieces[dome.id].meta["collider"]
        self.assertGreaterEqual(len(cb.collider), len(shell))
        door = self.heroes(cell, G.DORM_DOOR)[0]
        self.assertAlmostEqual(door.origin[0], -100.0)  # on the dorm stair's interactable
        statue = self.heroes(cell, G.STATUE_SCHOLAR)[0]
        self.assertEqual(statue.origin, (-100.0, 54.0, -20.0))  # the statue landmark
        chand = self.heroes(cell, G.CHANDELIER)[0]
        self.assertLessEqual(chand.origin[1] + dict(chand.params)["chain"], 54 + 9 + cb.pieces[dome.id].meta["boss"] + 1e-6)
        # 9 -> 8, the notice board took bookcase_s2's place.
        self.assertGreaterEqual(len(self.heroes(cell, G.BOOKCASE)), 8)
        self.assertEqual(len(self.heroes(cell, G.NOTICEBOARD)), 1)
        self.assertEqual(len(self.heroes(cell, "kit_drapes")), 5)
        self.assertNotIn("castle_ceiling", {m.material for m in cb.meshes.values() if m.triangles() > 40})
        for kid in ("craft_charms_house_c", "craft_transfiguration_house_c", "tell_cr_c_bookshelf"):
            self.assertTrue(any(f.src == f"props:{kid}" for f in cb.faces), kid)

    def test_library_map_cases_and_lamps(self):
        cell = "sf_library"
        cb = self.cbs[cell]
        mp = self.heroes(cell, G.FLOOR_MAP)[0]
        self.assertEqual(mp.origin, (-103.0, 18.0, 6.0))  # the Library Floor Map landmark
        cut = R.floor_cuts(cb.plan)
        self.assertEqual(cut, [(-106.5, 3.5, -99.5, 8.5)])
        for f in cb.faces:
            if f.src in ("floor",) or f.src.startswith("floor_border"):
                xs = [q[0] for q in f.pts]
                zs = [q[2] for q in f.pts]
                inside = min(xs) > -106.5 + 1e-6 and max(xs) < -99.5 - 1e-6 and min(zs) > 3.5 + 1e-6 and max(zs) < 8.5 - 1e-6
                self.assertFalse(inside, f.src)
        # 19 (the painting's door took the east wall's south case).
        self.assertGreaterEqual(len(self.heroes(cell, G.BOOKCASE)), 19)
        ids = [lt["id"] for lt in cb.lights]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(sorted(i for i in ids if "lamp" in i), ["sf_library_lamp_e_0", "sf_library_lamp_w_0"])
        self.assertEqual(len(cb.seats), 8)

    def test_trophy_room(self):
        cell = "trophy_room"
        cb = self.cbs[cell]
        arm = self.heroes(cell, G.ARMILLARY)[0]
        self.assertEqual(arm.origin, (4.0, 0.0, -23.0))  # the Hanging Armillary landmark
        self.assertGreaterEqual(self.reps[cell].stats["armillary_clear"], G.OVERHEAD_CLEARANCE)
        self.assertEqual(len(self.heroes(cell, G.TROPHY_CASE)), 12)  # case_w4 gave way to the panel
        self.assertEqual([lt["id"] for lt in cb.lights], ["trophy_room_armillary_0"])
        self.assertEqual(self.reps[cell].stats["doors_checked"], 3)  # + the panel's door

    def test_trophy_stair_flight_solid(self):
        cell = "trophy_stair"
        cb = self.cbs[cell]
        (fl,) = [u for u in cb.uses if u.role == "flight"]
        self.assertIs(dict(fl.params)["solid"], True)
        # Review fixes: the strip the walls leave open beside each step is closed (it showed the sky).
        (sk,) = [u for u in cb.uses if u.role == "skirt"]
        self.assertEqual((sk.piece, sk.run, sk.origin, sk.yaw), ("kit_stair_skirt", fl.run, fl.origin, fl.yaw))
        self.assertEqual(self.reps[cell].stats["zfight"], 0)
        self.assertEqual(cb.pieces[fl.id].meta["steps"], 53)
        self.assertLessEqual(self.reps[cell].stats["tread_error_max"], G.TREAD_RAMP_TOLERANCE)
        self.assertGreaterEqual(self.reps[cell].stats["overhead_min"], G.OVERHEAD_CLEARANCE)
        self.assertEqual(len(self.heroes(cell, "kit_lantern_hanging")), 2)

    def test_string_course_breaks_round_wall_pieces(self):
        # The string course under the windows never runs behind a bookcase, trophy case or dorm door.
        for cell, cb in self.cbs.items():
            ctx = R.context(self.b.layout, cb.plan, self.b.kit_entries, self.b.open_edges)
            walls = [w for w, h in zip(R._hero_wall_spans(ctx), R._m5b_wall_heroes(ctx)) if not h.startswith("kit_drapes")]
            for u in cb.uses:
                if u.role != "string":
                    continue
                xs = [q[0] if abs(math.sin(u.yaw)) < 0.5 else q[2] for f in cb.faces if f.src == u.id for q in f.pts]
                ys = [q[1] for f in cb.faces if f.src == u.id for q in f.pts]
                for axis, c, sign, a, b, y0, y1 in walls:
                    on_line = abs((u.origin[2] if axis == "z" else u.origin[0]) - c) < 1e-6
                    if on_line and y0 < max(ys) and y1 > min(ys):
                        self.assertFalse(min(xs) < b - 1e-6 and max(xs) > a + 1e-6, (cell, u.id, a, b))
        self.assertEqual([u.id for u in self.cbs["trophy_room"].uses if u.role == "string"], ["string:trophy_room:n0:1"])

    def test_placements_byte_identical(self):
        for cell, cb in self.cbs.items():
            ctx = R.context(self.b.layout, cb.plan, self.b.kit_entries, self.b.open_edges)
            self.assertEqual(R.to_json(R.placements(ctx)), R.to_json(cb.uses), cell)
        self.assertEqual(self.b.build("trophy_room").digest(), self.cbs["trophy_room"].digest())



# =====================================================================================
# Castle M5c (Facades): docs/art/castle-kit.md "Exterior"
# =====================================================================================

M5C_FACING = ("entrance_hall", "great_hall", "grand_staircase", "common_room_house_a")


def _facade_catalogue() -> dict:
    return {n: prm for n, prm in P.CATALOGUE.items() if P.parse(n)[0] in G.FACADE_PIECES or n in G.FACADE_PIECES}


class M5cFacadesPieceTests(unittest.TestCase):
    """pieces/facades.py: every facade piece in the catalogue within its
    triangle cap, with an LOD1 (grid.FACADE_LOD), outside faces only
    (castle_wall, castle_trim, castle_roof, castle_glass: assemble.py clones
    them), and the brief's shapes: buttresses stepping back, crenels with
    thickness, crocketed pinnacles, roofs over 45 degrees with gables, the
    outer tracery matching the inner window."""

    @classmethod
    def setUpClass(cls):
        cls.built = {n: P.build(n, **prm) for n, prm in _facade_catalogue().items()}

    def test_catalogue_caps_and_lod1(self):
        fams = {P.parse(n)[0] if P.parse(n)[1] is not None else n for n in self.built}
        self.assertEqual(fams, {G.BUTTRESS, G.PARAPET_CRENEL, G.PINNACLE, G.ROOF_SLATE, "kit_window_tracery_outer",
                                "kit_window_blind"})
        caps = {G.BUTTRESS: G.BUTTRESS_TRIS, G.PINNACLE: G.PINNACLE_TRIS, G.ROOF_SLATE: G.ROOF_TRIS,
                "kit_window_tracery_outer": G.TRACERY_OUTER_TRIS, "kit_window_blind": G.BLIND_WINDOW_TRIS}
        for name, p in self.built.items():
            self.assertTrue(G.PIECE_RE.match(name), name)
            fam = P.parse(name)[0] if P.parse(name)[1] is not None else name
            cap = caps.get(fam, G.PARAPET_TRIS_PER_M * P.CATALOGUE[name].get("length", 0))
            self.assertLessEqual(p.triangles(), cap, name)
            self.assertIsNotNone(p.lod1, name)
            self.assertLess(sum(len(f.pts) - 2 for f in p.lod1), p.triangles(), name)
            mats = {G.parse_face_tag(f.tag).material for f in p.faces + p.lod1}
            self.assertLessEqual(mats, {"castle_wall", "castle_trim", "castle_roof", "castle_glass"}, name)

    def test_buttress_steps_back(self):
        p = P.build("kit_buttress_24", proud=1.4, top_proud=0.9)
        fronts = sorted((min(q[1] for q in f.pts), f.pts[0][2]) for f in p.faces
                        if f.normal()[2] > 0.99 and all(abs(q[2] - f.pts[0][2]) < 1e-9 for q in f.pts))
        depths = [d for _, d in fronts]
        self.assertGreater(depths[0], depths[-1])  # the plinth and the first stage stand furthest out
        self.assertAlmostEqual(max(q[2] for f in p.faces for q in f.pts), 1.4 + 0.15)
        self.assertAlmostEqual(max(q[1] for f in p.faces for q in f.pts), 24.0)
        self.assertEqual(p.meta["top_depth"], 0.9)
        self.assertTrue([f for f in p.faces if 0.5 < f.normal()[1] < 0.9])  # weatherings
        with self.assertRaises(ValueError):
            P.build("kit_buttress_40")
        with self.assertRaises(ValueError):
            P.build("kit_buttress_12", proud=0.5)

    def test_parapet_crenels(self):
        for length in (2.0, 2.3, 3.7, 7.5, 13.3, 29.15, 47.76, 60.0):
            p = P.build(G.PARAPET_CRENEL, length=length)
            mer = p.meta["merlons"]
            for a, b in mer:
                self.assertTrue(G.PARAPET_MERLON[0] - 1e-6 <= b - a <= G.PARAPET_MERLON[1] + 1e-6, (length, a, b))
            for (_, a1), (b0, _) in zip(mer, mer[1:]):
                self.assertTrue(0.5 - 1e-6 <= b0 - a1 <= 0.8 + 1e-6, (length, a1, b0))
            self.assertLessEqual(p.triangles(), G.PARAPET_TRIS_PER_M * length, length)
            ys = [q[1] for f in p.faces for q in f.pts]
            self.assertAlmostEqual(max(ys), 1.6)
            self.assertGreater(p.meta["front"] - p.meta["back"], 0.3)  # thickness, no flat card
            xs = [q[0] for f in p.faces for q in f.pts]
            self.assertGreaterEqual(min(xs), -0.06 - 1e-9)
            self.assertLessEqual(max(xs), length + 0.06 + 1e-9)
        with self.assertRaises(ValueError):
            P.build(G.PARAPET_CRENEL, length=1.0)

    def test_pinnacle(self):
        for h in (3.0, 4.5, 6.0):
            p = P.build(G.PINNACLE, height=h)
            self.assertAlmostEqual(max(q[1] for f in p.faces for q in f.pts), h)
            self.assertLessEqual(p.triangles(), G.PINNACLE_TRIS)
        p = P.build(G.PINNACLE, height=4.5)
        # Crockets hook out beyond the spire's edges, gablets face the four sides.
        r_spire = max(math.hypot(q[0], q[2]) for f in p.lod1 for q in f.pts if q[1] > 2.5)
        self.assertGreater(max(math.hypot(q[0], q[2]) for f in p.faces for q in f.pts if 2.5 < q[1] < 4.0), r_spire * 0.5)
        with self.assertRaises(ValueError):
            P.build(G.PINNACLE, height=8.0)

    def test_roof_slate(self):
        p = P.build("kit_roof_slate_12x16", pitch=50.0)
        rise = 6.0 * math.tan(math.radians(50.0))
        self.assertAlmostEqual(p.meta["rise"], rise)
        slopes = [f for f in p.faces if f.tag == "castle_roof|world" and f.normal()[1] > 0.5]
        self.assertEqual(len(slopes), 3)  # two slopes and the ridge roll's top
        for f in slopes:
            n = f.normal()
            if abs(n[2]) > 0.1:
                # Over 45 degrees: the world UVs project on the slope's horizontal axis, so the
                # slates' courses run along the ridge.
                self.assertGreater(abs(n[2]), abs(n[1]))
        gables = [f for f in p.faces if f.tag == "castle_wall|world" and abs(f.normal()[0]) > 0.99]
        self.assertEqual(sorted(round(f.pts[0][0], 3) for f in gables if len(f.pts) == 3), [0.0, 16.0])
        self.assertLessEqual(p.triangles(), G.ROOF_TRIS)
        with self.assertRaises(ValueError):
            P.build("kit_roof_slate_12x16", pitch=30.0)

    def test_outer_tracery_matches_the_inner_window(self):
        outer = P.build("kit_window_tracery_outer_8x8", depth=1.2, top=5.2)
        inner = P.build("kit_window_tracery_8x8", depth=0.8, outer=False)
        self.assertLessEqual(outer.triangles(), G.TRACERY_OUTER_TRIS)
        self.assertEqual(outer.meta["lights"], inner.meta["lights"])
        self.assertAlmostEqual(outer.meta["spring"], inner.meta["spring"])
        tiles = lambda p: sorted({f.tag for f in p.faces if f.tag.startswith("castle_glass")})  # noqa: E731
        self.assertEqual(tiles(outer), tiles(inner))
        mull = lambda p: sorted(round((min(q[0] for q in f.pts) + max(q[0] for q in f.pts)) / 2, 3) for f in p.faces  # noqa: E731
                                if f.tag == "castle_trim|trim:window_frame" and f.normal()[2] > 0.99
                                and abs(max(q[0] for q in f.pts) - min(q[0] for q in f.pts) - 0.22) < 1e-6
                                and max(q[1] for q in f.pts) - min(q[1] for q in f.pts) > 2.0)
        self.assertEqual(mull(outer), mull(inner))
        pts = [q for f in outer.faces for q in f.pts]
        self.assertGreaterEqual(min(q[2] for q in pts), -1.2 - 1e-9)  # within the mass to the owner's outer face
        self.assertLessEqual(max(q[2] for q in pts), 0.35)  # proud at most a sill's depth (it stands at 10.8 m)
        self.assertGreaterEqual(min(q[1] for q in pts), -0.3)
        # The glass faces out only (the inner window's glass faces the hall).
        self.assertTrue(all(f.normal()[2] > 0.99 for f in outer.faces if f.tag.startswith("castle_glass")))


class M5cFacadesCellTests(unittest.TestCase):
    """The four facing cells built with their facades: lint clean within
    their draw caps (grid.draw_cap: the kind's plus FACADE_DRAWS), the outside
    faces on the `@ext` clones, the pieces each `facade` flag asks for, every
    facade piece's LOD1 at FACADE_LOD, the traceried window's hole open from
    the courtyard, placements byte-identical; cells without a facade carry no
    exterior clone."""

    @classmethod
    def setUpClass(cls):
        cls.b = builder()
        cls.cbs = {c: cls.b.build(c) for c in M5C_FACING}
        cls.reps = {c: LN.lint_cell(cb, cls.b) for c, cb in cls.cbs.items()}

    def facade(self, cell, fam):
        return [u for u in self.cbs[cell].uses if A.facade_piece(u.piece)
                and (P.parse(u.piece)[0] == fam or u.piece == fam)]

    def test_lint_clean_within_draw_caps(self):
        for cell, cb in self.cbs.items():
            rep = self.reps[cell]
            self.assertEqual(rep.errors, [], cell)
            kind = cb.plan.budget
            if G.BUDGETS[kind].cells == 1:  # One house's cup banners counted
                self.assertLessEqual(A.budget_draws(cb), G.draw_cap(cell, kind), cell)
            self.assertLessEqual(cb.triangles(), G.BUDGETS[kind].tris[1], cell)
            self.assertLessEqual(rep.stats["facade_tris"], G.FACADE_TRIS, cell)
        group = LN.lint_budget_group([self.cbs["grand_staircase"], self.b.build("grand_staircase_upper")], self.b.layout,
                                     self.b)
        self.assertEqual(group.errors, [])
        self.assertLessEqual(group.stats["draws"], G.BUDGETS["grand_staircase"].draws + G.FACADE_DRAWS)
        # The draws the facades add: the @ext wall clone, the roof, the hall's outer tracery's trim.
        self.assertEqual(self.cbs["entrance_hall"].draws(), 7 + 3)
        self.assertEqual(self.cbs["great_hall"].draws(), 12 + 2 + 4 + 8)  # Sand, cup banners
        self.assertEqual(self.cbs["common_room_house_a"].draws(), 10 + 2)  # + the storeys' @ext trim

    def test_exterior_clones(self):
        for cell, cb in self.cbs.items():
            wall = cb.plan.materials["castle_wall"]
            self.assertIn(f"{cell}.{wall}@ext", cb.meshes, cell)
            self.assertIn(f"{cell}.{wall}", cb.meshes, cell)  # the interior keeps its material
            for name, md in cb.meshes.items():
                self.assertEqual(name, G.node_name(cell, md.material, name.split(".", 2)[2] if name.count(".") == 2 else None))
                self.assertFalse(G.ext_base(md.material) in G.EXT_NEVER and md.material.endswith(G.EXT_SUFFIX), name)
            self.assertEqual(f"{cell}.castle_roof" in cb.meshes, cb.plan.facade.roof, cell)
            # Every face of the facade pieces outside: a clone, the roof, or glass.
            for f in cb.faces:
                if f.src.startswith("facade:") or f.src == "hero:fac_tracery_outer":
                    self.assertTrue(G.is_outdoor_material(f.material) or f.material in G.EXT_NEVER, (cell, f.src, f.material))
        for cell in ("gf_corridor_west", "ff_corridor_north", "grand_staircase_upper"):
            cb = self.b.build(cell)
            self.assertFalse([m.material for m in cb.meshes.values() if G.is_outdoor_material(m.material)], cell)
        self.assertEqual(A.ext_material_m5c("castle_wall@ext", {"castle_wall": "castle_wall_b"}), "castle_wall_b@ext")
        self.assertEqual(A.ext_material_m5c("castle_trim@ext", {}), "castle_trim@ext")
        self.assertEqual(A.ext_material_m5c("castle_glass@ext", {}), "castle_glass")
        self.assertEqual(A.ext_material_m5c("castle_wall", {"castle_wall": "castle_wall_c"}), "castle_wall_c")

    def test_pieces_follow_the_flags(self):
        hall, gh, gs, cra = (self.cbs[c] for c in M5C_FACING)
        self.assertEqual(len(self.facade("entrance_hall", G.ROOF_SLATE)), 1)
        # Two corner piers closing the corners the shell leaves open (no pinnacle under the gable), two
        # buttresses with their pinnacles.
        self.assertEqual(sorted(u.id for u in self.facade("entrance_hall", G.BUTTRESS)),
                         ["facade:buttress:s:0", "facade:buttress:s:1", "facade:pier:s:0", "facade:pier:s:1"])
        self.assertEqual(len(self.facade("entrance_hall", G.PINNACLE)), 2)
        self.assertEqual(len(self.facade("entrance_hall", G.TRACERY_OUTER)), 1)
        self.assertEqual(len(self.facade("great_hall", G.ROOF_SLATE)), 1)
        # Between the eight bays' lancets and at the east corner; a pier closing the open south-west corner.
        self.assertEqual(sorted(u.id for u in self.facade("great_hall", G.BUTTRESS)),
                         [f"facade:buttress:s:{k}" for k in range(8)] + ["facade:pier:s:0"])
        self.assertEqual(self.facade("great_hall", G.PARAPET_CRENEL), [])
        self.assertEqual(len(self.facade("grand_staircase", G.PARAPET_CRENEL)), 2)
        self.assertEqual(len(self.facade("grand_staircase", G.PINNACLE)), 3)
        self.assertEqual(self.facade("grand_staircase", G.ROOF_SLATE), [])
        self.assertEqual(len(self.facade("common_room_house_a", G.PARAPET_CRENEL)), 1)
        self.assertEqual(self.facade("common_room_house_a", G.PINNACLE), [])
        # The roofs' ridges run along the halls' long sides; their pitch is the rule's.
        for cell, along in (("entrance_hall", "z"), ("great_hall", "x")):
            u = self.facade(cell, G.ROOF_SLATE)[0]
            self.assertEqual(along, "x" if abs(u.yaw) < 1e-6 else "z", cell)
            self.assertTrue(G.ROOF_PITCH_DEG[0] < u.kwargs()["pitch"] <= G.ROOF_PITCH_DEG[1])
        # The Great Hall's buttresses stand between its south lancets, clear of their reveals.
        lancets = [o for o in gh.plan.openings if o.kind == "window" and o.axis == "z" and o.sign < 0]
        for u in self.facade("great_hall", G.BUTTRESS):
            for o in lancets:
                self.assertFalse(o.t0 - 0.6 < u.origin[0] < o.t1 + 0.6, (u.id, o.id))
        # Pinnacles on the buttresses stay under the eaves' cornice.
        for u in self.facade("great_hall", G.PINNACLE):
            if ":corner:" in u.id or ":pier:" in u.id:
                continue
            top = u.origin[1] + u.kwargs()["height"]
            self.assertLess(top, 29.6 - R.ROOF_CORNICE)

    def test_lod1_at_the_facade_distance(self):
        for cell, cb in self.cbs.items():
            for u in cb.uses:
                if u.id.startswith("facade:") and A.facade_piece(u.piece):
                    self.assertEqual(u.lod_distance, G.FACADE_LOD, u.id)
            got = {base: d for _, (md, base, d) in cb.lod_meshes.items() if G.is_outdoor_material(md.material)}
            self.assertTrue(got, cell)
            self.assertEqual(set(got.values()), {G.FACADE_LOD}, cell)
        # The hall's balustrades keep theirs.
        hall = self.cbs["entrance_hall"]
        others = {d for _, (md, base, d) in hall.lod_meshes.items() if not G.is_outdoor_material(md.material)}
        self.assertEqual(others, {R.LOD_DISTANCE})

    def test_window_open_from_the_courtyard(self):
        hall = self.cbs["entrance_hall"]
        x0, x1, y0, y1 = G.FRONT_WINDOW_OUTSIDE
        face = [f for f in hall.faces if f.src == "base" and all(abs(q[2] - 40.8) < 1e-6 for q in f.pts)]
        for f in face:
            xs, ys = [q[0] for q in f.pts], [q[1] for q in f.pts]
            self.assertFalse(min(xs) < 16 < max(xs) and min(ys) < 17.0 < max(ys), f.pts)
        reveal = [f for f in hall.faces if f.src == "base" and all(abs(q[0] - 12.0) < 1e-6 for q in f.pts)
                  and min(q[2] for q in f.pts) > 40.79]
        self.assertTrue(reveal)
        self.assertTrue(all(f.material.endswith(G.EXT_SUFFIX) for f in reveal))
        u = self.facade("entrance_hall", G.TRACERY_OUTER)[0]
        self.assertEqual(u.origin, (16.0, 10.8, 42.0))

    def test_placements_byte_identical(self):
        for cell, cb in self.cbs.items():
            ctx = R.context(self.b.layout, cb.plan, self.b.kit_entries, self.b.open_edges)
            self.assertEqual(R.to_json(R.placements(ctx)), R.to_json(cb.uses), cell)
        self.assertEqual(self.b.build("common_room_house_a").digest(), self.cbs["common_room_house_a"].digest())


class M5cFacadesCourtyardTests(unittest.TestCase):
    """The courtyard (built here with `build` flipped in memory: the Courtyard
    builder kits it): every face on its exterior clone, a parapet along its
    wall tops broken over the window over the front doors and at the gate,
    a pinnacle on each corner."""

    @classmethod
    def setUpClass(cls):
        b = A.Builder.from_files()
        if "courtyard" not in b.kit_plan.cells:  # (kitted on main since the Courtyard merge)
            b.kit_plan.cells["courtyard"] = b.kit_plan.planned["courtyard"]
        cls.b = b
        cls.cb = b.build("courtyard")

    def test_everything_outside(self):
        mats = sorted(m.material for m in self.cb.meshes.values() if m.material.startswith("castle_"))
        self.assertTrue(mats)  # (the greybox props the courtyard keeps until its pieces replace them: grey)
        for m in mats:
            self.assertTrue(G.is_outdoor_material(m), m)
        self.assertIn("castle_wall_b@ext", mats)

    def test_parapets_and_pinnacles(self):
        uses = [u for u in self.cb.uses if u.id.startswith("facade:")]
        corners = [u for u in uses if u.id.startswith("facade:pinnacle:corner:")]
        self.assertEqual(sorted((u.origin[0], u.origin[2]) for u in corners),
                         [(-18.5, 41.5), (-18.5, 90.5), (50.5, 41.5), (50.5, 90.5)])
        spans = []
        for u in uses:
            if u.piece != G.PARAPET_CRENEL:
                continue
            self.assertEqual(u.origin[1], G.COURT_WALL_TOP)
            pts = [q for f in self.cb.faces if f.src == u.id for q in f.pts]
            spans.append((min(q[0] for q in pts), max(q[0] for q in pts), min(q[2] for q in pts), max(q[2] for q in pts)))
        north = [s for s in spans if s[3] < 43.0]
        x0, x1 = G.FRONT_WINDOW_OUTSIDE[:2]
        self.assertEqual(len(north), 2)
        self.assertTrue(all(s[1] <= x0 or s[0] >= x1 for s in north))
        south = [s for s in spans if s[2] > 89.0]
        self.assertTrue(all(s[1] <= 12.0 or s[0] >= 20.0 for s in south))  # the gatehouse crowns the gate
        self.assertEqual(len(spans), 6)


# =====================================================================================
# Castle M5c (Courtyard): docs/art/castle-kit.md "The kitted cells".
# =====================================================================================

COURTYARD_CAPS = {G.ARCADE_BAY: G.ARCADE_TRIS, G.FOUNTAIN: G.FOUNTAIN_TRIS, G.GATEHOUSE: G.GATEHOUSE_TRIS,
                  G.LAMP_POST: G.LAMP_POST_TRIS, G.MARKET_STALL: G.MARKET_STALL_TRIS, G.PLANTER_TREE: G.PLANTER_TREE_TRIS,
                  G.NOTICE_WALL: G.NOTICE_WALL_TRIS, G.CRATE_STACK: G.CRATE_STACK_TRIS, G.PERCH_TREE: G.PERCH_TREE_TRIS}


class M5cCourtyardPieceTests(unittest.TestCase):
    """The courtyard pieces and the hall's court dressing: the
    catalogue, caps (every variant), palette-named colours only, the arcade's
    LOD1 and frame, the fountain's seats, the gatehouse's heights, the
    planters' crowns, the stalls' size and awnings, the lamp's light, bad
    arguments refused, determinism."""

    def test_catalogue_and_caps(self):
        self.assertEqual(set(COURTYARD_CAPS), {*G.COURTYARD_PIECES, *G.COURT_DRESSING_PIECES})
        for name in COURTYARD_CAPS:
            self.assertIn(name, P.CATALOGUE)
        variants = [(n, P.CATALOGUE[n]) for n in COURTYARD_CAPS]
        variants += [(G.MARKET_STALL, {"house": h, "goods": g}) for h in range(4) for g in range(G.STALL_GOODS)]
        variants += [(G.PLANTER_TREE, {"seed": k, "size": s, "height": h}) for k in range(6) for s in G.PLANTER_SIZE
                     for h in G.PLANTER_TREE_HEIGHT]
        variants += [(G.CRATE_STACK, {"seed": 1}), (G.ARCADE_BAY, {"pier0": 1, "pier1": 1, "end0": 2, "end1": 1})]
        for name, prm in variants:
            p = P.build(name, **prm)
            self.assertGreater(p.triangles(), 0, name)
            self.assertLessEqual(p.triangles(), COURTYARD_CAPS[name], (name, prm))
            self.assertTrue(p.walkthrough, name)
            for f in p.faces + (p.lod1 or []):
                ft = G.parse_face_tag(f.tag)
                if ft.material == "castle_props":
                    tile = ft.arg.split(":")[0]
                    self.assertEqual(":@" in ft.arg, kit.TILES[tile][2], (name, f.tag))
                self.assertFalse(ft.material.endswith(G.EXT_SUFFIX), (name, f.tag))  # assemble's split makes the clones

    def test_palette_names_and_slots(self):
        palette = PA.palette(TEXTURES_PATH)
        for name in ("water", "canvas", "leaf_light"):
            self.assertIn(name, palette)
        _, atlas = PA.build(PROPS_PATH, TEXTURES_PATH)
        for name in COURTYARD_CAPS:
            for prm in ([P.CATALOGUE[name]] + ([{"house": h, "goods": h} for h in range(4)] if name == G.MARKET_STALL else [])):
                for f in P.build(name, **prm).faces:
                    ft = G.parse_face_tag(f.tag)
                    if ft.mode == "kit":
                        tile, _, col = ft.arg.partition(":")
                        atlas.rect(tile, col or "-")  # raises if the slot is missing

    def test_arcade_bay(self):
        p = P.build(G.ARCADE_BAY)
        m = p.meta
        self.assertTrue(G.ARCADE_DEPTH[0] <= m["depth"] <= G.ARCADE_DEPTH[1])
        self.assertTrue(G.ARCADE_APEX[0] <= m["apex"] <= G.ARCADE_APEX[1])
        self.assertEqual(m["piers"], [-3.0])
        self.assertIsNotNone(p.lod1)
        self.assertLess(sum(len(f.pts) - 2 for f in p.lod1), p.triangles() / 3)
        mats = {G.parse_face_tag(f.tag).material for f in p.faces}
        self.assertEqual(mats, {"castle_wall", "castle_trim", "castle_wood", "castle_roof"})
        # Nothing behind the wall face; the roof reaches the wall over the walk, clear overhead.
        pts = [q for f in p.faces for q in f.pts]
        self.assertGreaterEqual(min(q[2] for q in pts), -0.1)
        under = [q[1] for f in p.faces if G.parse_face_tag(f.tag).material in ("castle_wood", "castle_roof") for q in f.pts]
        self.assertGreater(min(under), m["apex"])
        self.assertEqual(len(P.build(G.ARCADE_BAY, pier1=1, end1=2).meta["piers"]), 2)
        for bad in ({"depth": 4.0}, {"apex": 8.0}, {"pier": 1.2}):
            with self.assertRaises(ValueError):
                P.build(G.ARCADE_BAY, **bad)

    def test_fountain_gatehouse_lamp_planter_stall(self):
        f = P.build(G.FOUNTAIN)
        self.assertTrue(G.FOUNTAIN_RADIUS[0] <= f.meta["radius"] <= G.FOUNTAIN_RADIUS[1])
        top = max(q[1] for fc in f.faces for q in fc.pts)
        self.assertTrue(G.FOUNTAIN_HEIGHT[0] <= top <= G.FOUNTAIN_HEIGHT[1])
        self.assertEqual(len(f.meta["seats"]), 4)
        for x0, z0, x1, z1, y, _, front, back, drop in f.meta["seats"]:
            self.assertTrue(G.SEAT_TOP_RANGE[0] <= y <= G.SEAT_TOP_RANGE[1])
            self.assertEqual((front, back, drop), (0.26, 0.22, round(y - 0.12, 4)))  # the feet on the step
            depth, width = sorted((x1 - x0, z1 - z0))
            self.assertTrue(G.SEAT_ROW_DEPTH[0] <= depth <= G.SEAT_ROW_DEPTH[1])
            self.assertGreaterEqual(width, 2 * G.SEAT_WIDTH_MIN)
        self.assertTrue(any(G.parse_face_tag(fc.tag).arg == "glass:@water" for fc in f.faces))
        g = P.build(G.GATEHOUSE)
        self.assertGreaterEqual(g.meta["teeth"], G.PORTCULLIS_CLEAR)
        self.assertTrue(G.GATEHOUSE_TOP[0] <= max(q[1] for fc in g.faces for q in fc.pts) <= G.GATEHOUSE_TOP[1])
        # The gate's passage (|x| < 4) is clear of the gatehouse below the teeth.
        for fc in g.faces:
            if max(abs(q[0]) for q in fc.pts) < 4.0 - 1e-6:
                self.assertGreaterEqual(min(q[1] for q in fc.pts), g.meta["teeth"] - 1e-6)
        self.assertTrue(g.meta["collider"])
        lamp = P.build(G.LAMP_POST)
        self.assertEqual(len(lamp.meta["lights"]), 1)
        self.assertNotIn("lights", P.build(G.LAMP_POST, light=0).meta)
        self.assertTrue(any(G.parse_face_tag(fc.tag).arg == "glow:@glow_lantern" for fc in lamp.faces))
        for k in range(6):
            pt = P.build(G.PLANTER_TREE, seed=k)
            self.assertGreaterEqual(pt.meta["crown_low"], G.OVERHEAD_CLEARANCE)
            self.assertTrue(G.PLANTER_TREE_HEIGHT[0] <= pt.meta["height"] <= G.PLANTER_TREE_HEIGHT[1])
        w, d, h = G.MARKET_STALL_SIZE
        for house in range(4):
            st = P.build(G.MARKET_STALL, house=house, goods=house)
            pts = [q for fc in st.faces for q in fc.pts]
            self.assertLessEqual(max(q[1] for q in pts), h + 1e-6)
            self.assertLessEqual(max(q[0] for q in pts) - min(q[0] for q in pts), w + 0.21)  # the awning's overhang
            self.assertEqual(st.meta["house"], G.STALL_AWNINGS[house])
            self.assertTrue(any(G.parse_face_tag(fc.tag).arg == f"fabric:@{G.STALL_AWNINGS[house]}" for fc in st.faces))
        for bad in (lambda: P.build(G.MARKET_STALL, house=4), lambda: P.build(G.MARKET_STALL, goods=4),
                    lambda: P.build(G.PLANTER_TREE, size=3.0), lambda: P.build(G.LAMP_POST, height=5.0),
                    lambda: P.build(G.GATEHOUSE, top=27.0), lambda: P.build(G.PERCH_TREE, height=4.0)):
            with self.assertRaises(ValueError):
                bad()

    def test_court_dressing(self):
        self.assertTrue(2.4 <= P.build(G.PERCH_TREE).meta["height"] <= 3.4)
        nw = P.build(G.NOTICE_WALL)
        self.assertTrue(any(G.parse_face_tag(fc.tag).arg == "fabric:@cork" for fc in nw.faces))
        mats = {G.parse_face_tag(fc.tag).material for n in G.COURT_DRESSING_PIECES for fc in P.build(n).faces}
        self.assertLessEqual(mats, {"castle_props", "castle_wood"})  # no new draw in the hall

    def test_deterministic(self):
        for name in COURTYARD_CAPS:
            a, b = P.build(name, **P.CATALOGUE[name]), P.build(name, **P.CATALOGUE[name])
            self.assertEqual([(f.pts, f.tag, f.uv) for f in a.faces], [(f.pts, f.tag, f.uv) for f in b.faces], name)


class M5cCourtyardCellTests(unittest.TestCase):
    """The courtyard built and the hall's court dressing:
    lint clean within the budgets, the brief's heroes, the greybox fountain
    replaced, the tells kept, six lamp lights with their own ids, the arcade's
    LOD1 at FACADE_LOD, the gate arch without quoins, the hall within 11
    draws and 120 k, placements byte-identical."""

    @classmethod
    def setUpClass(cls):
        cls.b = builder()
        cls.cy = cls.b.build("courtyard")
        cls.eh = cls.b.build("entrance_hall")
        cls.rep = LN.lint_cell(cls.cy, cls.b)
        cls.eh_rep = LN.lint_cell(cls.eh, cls.b)

    def heroes(self, cb, piece):
        return [u for u in cb.uses if u.role == "hero" and u.piece == piece]

    def test_built_and_lint_clean_within_budget(self):
        cb, rep = self.cy, self.rep
        self.assertNotIn("courtyard", self.b.kit_plan.planned)
        self.assertEqual(rep.errors, [])
        self.assertEqual(cb.plan.budget, "courtyard")
        self.assertLessEqual(cb.draws(), G.draw_cap("courtyard", "courtyard"))
        self.assertLessEqual(cb.triangles(), G.BUDGETS["courtyard"].tris[1])
        self.assertLessEqual(cb.collider_triangles(), G.COLLIDER_TRIS)
        st = rep.stats
        self.assertLessEqual(st["loops"]["added"], G.slice_allowance("courtyard", st["loops"]["unsliced"]))
        for k in ("coverage_missing", "zfight", "degenerate", "uv_bad", "off_wall", "window_depth_bad"):
            self.assertEqual(st[k], 0, k)
        meshes = list(cb.meshes.values()) + [md for md, _, _ in cb.lod_meshes.values()]  # the arcade's LOD1 too
        self.assertEqual(st["shade"]["corners"], sum(len(f) for m in meshes for f in m.faces))
        self.assertFalse([m for m in cb.meshes.values() if not m.material.startswith("castle_")])
        self.assertIn("castle_floor_court", {m.material for m in cb.meshes.values()})

    def test_the_brief(self):
        cb = self.cy
        self.assertEqual(sorted(cb.plan.replaces), ["fountain_basin", "fountain_column"])
        self.assertEqual(len(self.heroes(cb, G.ARCADE_BAY)), 25)  # 19, and the south arcade's 6 (castle M5c review)
        (fountain,) = self.heroes(cb, G.FOUNTAIN)
        self.assertEqual((fountain.origin[0], fountain.origin[2]), G.FOUNTAIN_CENTRE)
        self.assertEqual(len(self.heroes(cb, G.GATEHOUSE)), 1)
        stalls = self.heroes(cb, G.MARKET_STALL)
        self.assertTrue(3 <= len(stalls) <= 6)
        self.assertEqual({dict(u.params)["house"] for u in stalls}, {0.0, 1.0, 2.0, 3.0})
        self.assertGreaterEqual(len(self.heroes(cb, G.PLANTER_TREE)), 3)
        self.assertGreaterEqual(len(self.heroes(cb, "kit_noticeboard")), 1)
        self.assertGreaterEqual(len([u for u in cb.uses if u.role == "hero" and u.piece.startswith("kit_bench_")]), 1)
        for kid in ("tell_courtyard_corner", "tell_courtyard_arch"):
            self.assertTrue(any(f.src == f"props:{kid}" for f in cb.faces), kid)
        # Seats: the benches' rows and the fountain's coping.
        self.assertGreaterEqual(len(cb.seats), 4 + len([u for u in cb.uses if u.piece.startswith("kit_bench_")]))

    def test_lights_lod_and_gate_arch(self):
        cb = self.cy
        ids = [lt["id"] for lt in cb.lights]
        self.assertEqual(len(ids), 6)
        self.assertEqual(len(set(ids)), 6)
        self.assertLessEqual(len(ids), kit.LIGHTS_PER_CELL)
        self.assertTrue(all(i.startswith("courtyard_lamp_") for i in ids))
        for u in self.heroes(cb, G.ARCADE_BAY):
            self.assertEqual(u.lod_distance, G.FACADE_LOD)
        self.assertTrue(cb.lod_meshes)
        self.assertEqual({d for _, _, d in cb.lod_meshes.values()}, {G.FACADE_LOD})
        (gate,) = [u for u in cb.uses if u.role == "arch" and u.id.endswith(":courtyard_gate")]
        self.assertFalse(dict(gate.params)["quoins"])
        self.assertTrue(all(dict(u.params).get("quoins", True) for u in cb.uses if u.role == "arch" and u is not gate))

    def test_hall_court_dressing(self):
        cb, rep = self.eh, self.eh_rep
        self.assertEqual(rep.errors, [])
        self.assertLessEqual(cb.draws(), G.BUDGETS["entrance_hall"].draws)
        self.assertLessEqual(cb.triangles(), 120_000)
        court = [u for u in cb.uses if u.id.startswith("hero:court_")]
        self.assertEqual({P.parse(u.piece)[0] for u in court}, {G.RUG, G.NOTICE_WALL, G.PERCH_TREE, G.CRATE_STACK})
        x0, z0, x1, z1 = G.EH_COURT
        for u in court:
            self.assertTrue(x0 <= u.origin[0] <= x1 and z0 <= u.origin[2] <= z1, u.id)
        self.assertEqual({m.material for m in cb.meshes.values()} & {"castle_props", "castle_wood"},
                         {"castle_props", "castle_wood"})

    def test_placements_byte_identical(self):
        for cb in (self.cy, self.eh):
            ctx = R.context(self.b.layout, cb.plan, self.b.kit_entries, self.b.open_edges)
            self.assertEqual(R.to_json(R.placements(ctx)), R.to_json(cb.uses), cb.cell)
        self.assertEqual(self.b.build("courtyard").digest(), self.cy.digest())


# =====================================================================================
# Castle M5c review: docs/art/castle-kit.md
# =====================================================================================


class M5cReviewTests(unittest.TestCase):
    """Review fixes: blind lancets on the west range and common room
    A's tower (every storey above the courtyard's wall, outside faces only,
    nothing behind the face), the gatehouse's overhang closed down to the
    courtyard's 1 m outer wall."""

    @classmethod
    def setUpClass(cls):
        cls.b = builder()

    def test_blind_window_piece(self):
        p = P.build(G.BLIND_WINDOW)
        self.assertLessEqual(p.triangles(), G.BLIND_WINDOW_TRIS)
        self.assertTrue(p.lod1)
        glass = [f for f in p.faces if G.parse_face_tag(f.tag).material == "castle_glass"]
        self.assertEqual(len(glass), 1)
        self.assertGreater(min(q[2] for q in glass[0].pts), 0.03)  # off the wall face: no z-fighting with it
        self.assertGreaterEqual(min(q[2] for f in p.faces + p.lod1 for q in f.pts), -1e-9)  # nothing behind the face
        self.assertGreaterEqual(min(q[1] for f in p.faces for q in f.pts), -0.3)

    def test_west_range_and_tower_windows(self):
        # The east wing (N9) and the seventh-floor east corridor (N11) stand against the
        # Grand Staircase's east face, so 16 of its 60 blind windows go.
        for cell, count in (("grand_staircase", 44), ("common_room_house_a", 18)):
            cb = self.b.build(cell)
            ws = [u for u in cb.uses if u.piece == G.BLIND_WINDOW]
            self.assertEqual(len(ws), count, cell)
            for u in ws:
                self.assertEqual(u.lod_distance, G.FACADE_LOD)
                self.assertGreater(u.origin[1], G.COURT_WALL_TOP)
                self.assertAlmostEqual((u.origin[1] - R.BLIND_SILL) % G.STOREY, 0.0, places=4)
            for f in cb.faces:
                if f.src.startswith("facade:window:"):
                    self.assertTrue(G.is_outdoor_material(f.material) or f.material in G.EXT_NEVER, (cell, f.material))
            rep = LN.lint_cell(cb, self.b)
            self.assertEqual(rep.errors, [], cell)
            self.assertLessEqual(rep.stats["facade_tris"], G.FACADE_TRIS, cell)

    def test_gatehouse_overhang_closed(self):
        p = P.build(G.GATEHOUSE)
        top = G.COURT_WALL_TOP
        under = [f for f in p.faces if f.normal()[1] < -0.99 and all(abs(q[1] - top) < 1e-6 for q in f.pts)]
        zs = sorted({round(q[2], 4) for f in under for q in f.pts})
        self.assertIn(-L.EXTERIOR_THICKNESS["courtyard"], zs)
        self.assertNotIn(-2.0, zs)


# =====================================================================================
# Docs/art/castle-kit.md "Exterior". The courtyard's grounds side.
# =====================================================================================


class P3aExteriorPieceTests(unittest.TestCase):
    """kit_corbel_table_<w>: stepped corbels in front of the wall face, their
    tops at the overhang, within their triangles."""

    def test_corbel_table(self):
        from castle_kit.pieces import exterior as EX

        p = P.build("kit_corbel_table_18")
        n = p.meta["corbels"]
        self.assertGreaterEqual(n, 12)
        self.assertLessEqual(p.triangles(), EX.CORBEL_TRIS * n)
        pts = [q for f in p.faces for q in f.pts]
        self.assertGreaterEqual(min(q[2] for q in pts), -1e-9)  # never behind the wall face
        self.assertAlmostEqual(max(q[1] for q in pts), G.COURT_WALL_TOP)
        self.assertLessEqual(max(abs(q[0]) for q in pts), 9.0)
        self.assertTrue(all(G.parse_face_tag(f.tag).material == "castle_trim" for f in p.faces))
        with self.assertRaises(ValueError):
            P.build("kit_corbel_table_0.5")

    def test_in_the_catalogue(self):
        self.assertIn("kit_corbel_table_18", P.CATALOGUE)


class P3aExteriorCourtyardTests(unittest.TestCase):
    """The courtyard built: its walls' faces beyond its outer planes (the
    grounds side, the gatehouse's overhang) in the outside stone's castle_wall_c
    clone, its own stone inside; the corbel table under the gatehouse's
    overhang; lint clean within its draw cap (+1 draw)."""

    @classmethod
    def setUpClass(cls):
        cls.b = builder()
        cls.cb = cls.b.build("courtyard")
        cls.rep = LN.lint_cell(cls.cb, cls.b)

    def test_lint_clean_within_its_cap(self):
        self.assertEqual(self.rep.errors, [])
        self.assertLessEqual(self.cb.draws(), G.draw_cap("courtyard", "courtyard"))
        self.assertLessEqual(self.cb.triangles(), G.BUDGETS["courtyard"].tris[1])
        st = self.rep.stats["p3a_exterior"]
        self.assertGreater(st["outside_faces"], 0)
        self.assertGreaterEqual(st["corbels"], 12)

    def test_outer_faces_in_the_outside_stone(self):
        stone = G.ext_name(G.P3A_EXTERIOR_STONE)
        self.assertEqual(stone, "castle_wall_c@ext")
        self.assertIn(stone, {m.material for m in self.cb.meshes.values()})
        self.assertIn("castle_wall_b@ext", {m.material for m in self.cb.meshes.values()})  # inside: unchanged
        planes = A.outer_planes_p3a(self.b, "courtyard")
        self.assertIn((2, 91.0, 1.0), planes)  # the grounds side
        south = [f for f in self.cb.faces if f.src == "base" and f.material.startswith("castle_wall")
                 and all(abs(p[2] - 91.0) < 1e-6 for p in f.pts) and f.normal()[2] > 0.99]
        self.assertTrue(south)
        self.assertTrue(all(f.material == stone for f in south))
        inner = [f for f in self.cb.faces if f.src == "base" and f.material.startswith("castle_wall")
                 and all(abs(p[2] - 90.0) < 1e-6 for p in f.pts) and f.normal()[2] < -0.99]
        self.assertTrue(inner)
        self.assertTrue(all(f.material != stone for f in inner))

    def test_corbels_under_the_overhang(self):
        (u,) = [u for u in self.cb.uses if u.id == "hero:ext_gatehouse_corbels"]
        pts = [q for f in self.cb.faces if f.src == u.id for q in f.pts]
        self.assertTrue(pts)
        self.assertGreaterEqual(min(q[2] for q in pts), 91.0 - 1e-6)  # outside the outer face
        self.assertLessEqual(max(q[2] for q in pts), 92.3 + 1e-6)  # under the overhang (TOWER_BACK beyond)
        self.assertAlmostEqual(max(q[1] for q in pts), G.COURT_WALL_TOP)
        self.assertGreaterEqual(min(q[0] for q in pts), 7.0 - 1e-6)
        self.assertLessEqual(max(q[0] for q in pts), 25.0 + 1e-6)

    def test_other_cells_untouched(self):
        """exterior_stone_p3a changes the courtyard only: a facing cell builds
        byte-identically with it turned off."""
        cell = "entrance_hall"
        with_it = self.b.build(cell).digest()
        orig = A.exterior_stone_p3a
        try:
            A.exterior_stone_p3a = lambda *a, **k: None
            without = self.b.build(cell).digest()
        finally:
            A.exterior_stone_p3a = orig
        self.assertEqual(with_it, without)
