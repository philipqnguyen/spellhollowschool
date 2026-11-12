"""Unit tests for the castle kit contracts (castle kit M1, M2, M3 + M4, D1, M5a): the grid
and bay solver (castle_kit/grid.py), the texture set and sheet layouts
(data/art/castle_textures.json), the shared props atlas
(castle_kit/props_atlas.py) and castle_kit.json validation
(castle_kit/schema.py), with the Entrance Hall's M2 placements. Pure
Python; run through tests/run_tests.py."""

import copy
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.dirname(HERE), os.path.join(os.path.dirname(HERE), "world")]

import layout_lib as L  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import props_atlas as PA  # noqa: E402
from castle_kit import schema as S  # noqa: E402
from castle_kit import textures as T  # noqa: E402
import kit  # noqa: E402

KIT_PATH = os.path.join(REPO, "data", "world", "castle_kit.json")
LAYOUT_PATH = os.path.join(REPO, "data", "world", "layout.json")
PROPS_PATH = os.path.join(REPO, "data", "world", "props.json")
TEXTURES_PATH = os.path.join(REPO, "data", "art", "castle_textures.json")
TOON_PATH = os.path.join(REPO, "data", "art", "toon_palette.json")
TEST_BED = ("gf_corridor_west", "ff_corridor_north")
KITTED = TEST_BED + ("entrance_hall",)  # castle kit M2: the hall is built since its pieces exist
KITTED += ("great_hall",)  # castle kit M3: built since its pieces exist
KITTED += ("grand_staircase", "grand_staircase_upper", "l7_corridor_north")  # castle kit M4: the Grand Staircase's cells
# Castle kit M3 + M4: every cell of those milestones is built.
# Castle M5a: the eight M5a cells are planned (`"build": false`) until
# their pieces exist; each part of the kit adds its own line here.
KITTED += ("sf_corridor_west", "tf_corridor_west", "l4_corridor_west", "l5_corridor_west", "l6_corridor_west",
           "gf_undercroft", "ff_gallery_east")  # castle M5a, Corridors: built
KITTED += ("west_tower_stair",)  # castle M5a, Tower: built since its pieces exist
# Castle M5b: the twelve M5b cells likewise, each part its own KITTED line.
KITTED += ("dungeon_west", "basement_east", "dungeon_classroom", "common_room_house_b", "common_room_house_d",
           "kitchens", "scullery_passage")  # castle M5b, Dungeons: built
KITTED += ("common_room_house_a", "common_room_house_c", "sf_library", "trophy_room", "trophy_stair")  # castle M5b, Upper rooms: built
# Castle M5c: the courtyard likewise.
KITTED += ("courtyard",)  # castle M5c, Courtyard: built since its pieces exist
KITTED += ("clock_courtyard", "covered_bridge", "tf_corridor_east", "hospital_wing")  # Wings A: built
KITTED += ("owl_loft", "l7_corridor_east", "dungeon_cellars")  # Wings B: built
KITTED += ("passage_dungeon", "passage_painting", "passage_panel", "passage_statue", "passage_tapestry")  # Secrets: built
PLANNED = tuple(c for c in (*G.M5A_CELLS, *G.M5B_CELLS, *G.M5C_CELLS, *G.P3C_CELLS, *G.P3D_CELLS) if c not in KITTED)


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def surf(sid, x0, z0, x1, z1, y=0.0, kind="room", ceiling=8.4, cell="c"):
    d = {"id": sid, "kind": kind, "name": sid, "level": 0, "cell": cell, "min": [x0, z0], "max": [x1, z1], "y": y}
    if ceiling is not None:
        d["ceiling"] = ceiling
    return d


def layout(surfaces):
    return L.parse_layout({"version": 1, "surfaces": surfaces, "spawns": [{"id": "s", "pos": [1, 0, 1], "yaw": 0}]})


class GridTests(unittest.TestCase):
    def test_storeys_courses_and_tiles_line_up(self):
        self.assertAlmostEqual(G.CORRIDOR_CEILING + G.SLAB, G.STOREY)
        self.assertAlmostEqual(G.COURSES_PER_STOREY * G.COURSE, G.STOREY)
        self.assertAlmostEqual(G.STONE_TILE_M / G.COURSE, round(G.STONE_TILE_M / G.COURSE))
        self.assertEqual([G.level_y(n) for n in (-2, 0, 7)], [-18.0, 0.0, 63.0])
        self.assertAlmostEqual(G.STONE_PX_PER_M, 1024 / 4.8)

    def test_two_centred_arches(self):
        # 4.5 m clear 0.3 m inside each jamb of a 5 x 6 arch.
        self.assertAlmostEqual(G.arch_clear_height(5.0, 6.0), 4.5, delta=0.01)
        for w, h in G.ARCH_SIZES:
            self.assertAlmostEqual(G.arch_height(w, h, 0.0), h)
            self.assertAlmostEqual(G.arch_height(w, h, w / 2), G.arch_springing(w, h))
            self.assertGreaterEqual(G.arch_clear_height(w, h), G.DOOR_CLEAR_HEIGHT, (w, h))
            # The springing line stays above the intrusion zone: arch heads never narrow the walk.
            self.assertGreaterEqual(G.arch_springing(w, h), G.INTRUSION_HEIGHT, (w, h))
            heights = [G.arch_height(w, h, w / 2 * i / 10) for i in range(11)]
            self.assertEqual(heights, sorted(heights, reverse=True), (w, h))

    def test_mouldings_respect_the_intrusion_rule_and_catch_outlines(self):
        for m in (G.PLINTH, G.STRING, G.SHAFT):
            self.assertTrue(G.intrusion_ok(m.proud, 0.0), m.name)
        for m in G.MOULDINGS.values():
            self.assertGreaterEqual(m.proud, G.MIN_RELIEF, m.name)
        self.assertGreaterEqual(G.CORRIDOR_CEILING - G.CORNICE.height, G.INTRUSION_HEIGHT)
        self.assertFalse(G.intrusion_ok(0.3, 1.0))
        self.assertTrue(G.intrusion_ok(0.3, 2.4))
        self.assertTrue(G.intrusion_ok(1.0, 0.5, walkthrough=True))
        self.assertLess(G.INTRUSION_MAX, G.CHARACTER_RADIUS)

    def test_openings_fit_a_corridor(self):
        lancet = G.OPENING_PIECES["kit_window_lancet_1.6x5"]
        self.assertLessEqual(2.4 + lancet.height, G.CORRIDOR_CEILING - G.CORNICE.height)
        for name, spec in G.OPENING_PIECES.items():
            self.assertTrue(G.PIECE_RE.match(name), name)
            self.assertTrue(name.endswith(f"_{G.fmt_m(spec.width)}x{G.fmt_m(spec.height)}"), name)
            self.assertIn(spec.kind, G.OPENING_KINDS)
            if spec.depth is not None:
                self.assertLessEqual(spec.depth, G.EXTERIOR_SHELL - G.REVEAL_MARGIN + 1e-9, name)
            if name == "kit_window_tracery_8x8":
                # M2: over the front doors, exactly their width.
                self.assertEqual((spec.width, spec.height, spec.depth), (8.0, 8.0, None))
                continue
            if name == "kit_window_rose_6x6":
                # Castle M5a: the Long Gallery's east end wall, one 10 m bay.
                self.assertLessEqual(spec.width + 2 * G.OPENING_MARGIN, 10.0 + 1e-9, name)
                continue
            if name in ("kit_window_end_9x12", "kit_fireplace_hooded_4.4x3.6"):
                # M3: the Great Hall's end wall (one 22 m bay) and fireplace bay (7.5 m).
                self.assertLessEqual(spec.width + 2 * G.OPENING_MARGIN, (22.0 if spec.kind == "window" else 7.5) + 1e-9, name)
                continue
            # Every opening fits a bay at the solver's narrowest corridor bay, clear of the shafts.
            self.assertLessEqual(spec.width + 2 * G.OPENING_MARGIN, 6.0 * (1 - G.BAY_TOLERANCE) + 0.01, name)

    def test_piece_names(self):
        self.assertEqual(G.arch_piece(5, 6), "kit_stone_arch_5x6")
        self.assertEqual(G.arch_piece(3, 4.5), "kit_stone_arch_3x4.5")
        # M2: the plain shaft is the repeated one; the clustered M1 shaft stays for hero spots.
        self.assertEqual(G.shaft_piece(8.4), "kit_stone_shaft_8.4_plain")
        self.assertEqual(G.shaft_piece(14, "plain"), "kit_stone_shaft_14_plain")
        self.assertEqual(G.shaft_piece(8.4, "clustered"), "kit_stone_shaft_8.4")
        with self.assertRaises(ValueError):
            G.shaft_piece(8.4, "fluted")
        self.assertLessEqual(G.SHAFT_TRIS["plain"], 200)
        self.assertEqual(G.piece_name("stone", "plinth"), "kit_stone_plinth")
        self.assertEqual(G.piece_name("door", "oak", 8, 10, "open"), "kit_door_oak_8x10_open")
        self.assertEqual(G.piece_name("lantern", "wall"), "kit_lantern_wall")
        self.assertEqual(G.fmt_m(1.60), "1.6")
        self.assertEqual(G.piece_name("niche", None, 2, 3.5), "kit_niche_2x3.5")
        self.assertEqual(G.piece_name("threshold", w=5), "kit_threshold_5")
        for bad in (("Stone", "arch"), ("stone", "Arch"), ("stone", "arch_")):
            with self.assertRaises(ValueError):
                G.piece_name(*bad)
        for bad in ("kit_", "kit_window_lancet_1L_1.6x5", "prop_stone_arch", "kit_stone_arch_5x6_"):
            self.assertIsNone(G.PIECE_RE.match(bad), bad)

    def test_bay_solver(self):
        cases = {26.0: 4, 68.0: 11, 40.0: 7, 20.0: 3, 12.0: 2, 5.0: 1, 2.0: 1, 6.0: 1}
        for length, n in cases.items():
            b = G.solve_bays(length, 6.0)
            self.assertEqual(b.n, n, length)
            self.assertAlmostEqual(b.width * b.n, length)
        self.assertTrue(G.solve_bays(68.0, 6.0).within)
        self.assertFalse(G.solve_bays(8.0, 6.0).within)
        # Ties go to fewer, wider bays; forced counts are reported honestly.
        self.assertEqual(G.solve_bays(8.0, 6.0).n, 1)
        forced = G.solve_bays(12.0, 6.0, count=1)
        self.assertEqual((forced.n, forced.within), (1, False))
        self.assertEqual(G.solve_bays(26.0, 6.0).bounds(-28.0), [-28.0, -21.5, -15.0, -8.5, -2.0])
        for bad in ((0.0, 6.0), (10.0, 0.0)):
            with self.assertRaises(ValueError):
                G.solve_bays(*bad)
        with self.assertRaises(ValueError):
            G.solve_bays(10.0, 6.0, count=0)

    def test_fixed_bays(self):
        b = G.fixed_bays(11.0, 40.0, [15.0, 20.0], 5.0)
        self.assertEqual((b.n, b.bounds(11.0), b.within), (3, [11.0, 15.0, 20.0, 40.0], True))
        self.assertAlmostEqual(b.width, 29.0 / 3)
        one = G.fixed_bays(0.0, 6.0, [], 5.0)
        self.assertEqual((one.n, one.bounds(0.0)), (1, [0.0, 6.0]))
        for bad in ([20.0, 15.0], [11.0], [40.0], [15.0, 15.0]):
            with self.assertRaises(ValueError):
                G.fixed_bays(11.0, 40.0, bad, 5.0)
        # Solved bays still compute their bounds.
        self.assertEqual(G.solve_bays(12.0, 6.0).bounds(1.0), [1.0, 7.0, 13.0])

    def test_open_leaves_and_door_constants(self):
        self.assertEqual(G.leaf_width(8.0), 4.0)
        self.assertEqual(G.LEAF_PROUD, G.INTRUSION_MAX)
        self.assertTrue(G.intrusion_ok(G.LEAF_PROUD, 0.0))
        # Leaves in a reveal leave the door's clear metre.
        for w, _ in G.ARCH_SIZES:
            self.assertGreaterEqual(w - 2 * G.LEAF_PROUD, G.DOOR_CLEAR_WIDTH)
        self.assertAlmostEqual(G.OVER_DOOR_CLEAR, G.COURSE)

    def test_glow_colours_stay_warm(self):
        def rgb(h):
            return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))

        lit_max = 1.3  # the toon ramp's brightest lit factor, generously
        for name in (G.GLOW_LANTERN, G.GLOW_CANDLE):
            r, g, b = rgb(name)
            # On screen before bloom: the emission at GLOW_LEVEL plus at most the base share lit.
            k = G.GLOW_LEVEL + G.GLOW_BASE_MAX * lit_max
            sr, sg, sb = (min(1.0, c * k) for c in (r, g, b))
            lo, hi = G.GLOW_BAND["r"]
            self.assertTrue(lo <= sr <= hi, name)
            self.assertTrue(G.GLOW_BAND["g"][0] <= sg <= G.GLOW_BAND["g"][1], (name, sg))
            self.assertTrue(G.GLOW_BAND["b"][0] <= sb <= G.GLOW_BAND["b"][1], (name, sb))
            self.assertGreaterEqual(sr, 0.95)  # the bloom's bright pass threshold
        tex = load(TEXTURES_PATH)["materials"]["castle_props"]
        self.assertEqual(tex["emissive_strength"], G.GLOW_LEVEL)

    def test_visibility_constants(self):
        self.assertEqual(G.SEES_DOORS, 2)
        self.assertEqual(G.SEES_FACADE, G.WALL_MASS_MAX)
        self.assertEqual(G.FRAME_WORLD_DRAWS, 50)
        self.assertEqual(G.BUDGETS["entrance_hall"], G.Budget(11, (80_000, 120_000)))

    def test_face_tags(self):
        self.assertEqual(G.parse_face_tag("castle_wall|world"), G.FaceTag("castle_wall", "world", None))
        self.assertEqual(G.parse_face_tag("castle_trim|trim:cornice").arg, "cornice")
        self.assertEqual(G.parse_face_tag("castle_glass|fit:light_lancet").mode, "fit")
        self.assertEqual(G.parse_face_tag("castle_props|kit:metal:#3A3530").arg, "metal:#3A3530")
        self.assertEqual(G.face_tag("castle_props", "kit:flame"), "castle_props|kit:flame")
        for bad in ("wall|world", "castle_wall|planar", "castle_trim|trim:", "castle_props|kit:metal:#3a3530",
                    "castle_wall world"):
            with self.assertRaises(ValueError):
                G.parse_face_tag(bad)

    def test_world_uvs_run_to_the_right_and_register_courses(self):
        T_M = G.STONE_TILE_M
        # Walls: u grows to the viewer's right, v up; y = 0.6 is one course (128 px of 1024).
        self.assertEqual(G.world_uv((4.8, 0.6, 0), (0, 0, 1), T_M), (1.0, 0.125))
        self.assertEqual(G.world_uv((4.8, 0.6, 0), (0, 0, -1), T_M), (-1.0, 0.125))
        self.assertEqual(G.world_uv((0, 1.2, 4.8), (1, 0, 0), T_M), (-1.0, 0.25))
        self.assertEqual(G.world_uv((0, 1.2, 4.8), (-1, 0, 0), T_M), (1.0, 0.25))
        self.assertEqual(G.world_uv((4.8, 0, 4.8), (0, 1, 0), T_M), (1.0, -1.0))
        self.assertEqual(G.world_uv((4.8, 8.4, 4.8), (0, -1, 0), T_M), (1.0, 1.0))
        # A storey is a whole number of courses: v at every floor is a course line.
        for level in G.LEVELS:
            v = G.world_uv((0, G.level_y(level), 0), (0, 0, 1), T_M)[1]
            self.assertAlmostEqual(v * 8, round(v * 8))
        # Ties prefer y, then x.
        self.assertEqual(G.world_uv((4.8, 0, 2.4), (0.7071, 0.7071, 0), T_M), (1.0, -0.5))

    def test_strip_and_tile_uvs(self):
        sheets = load(TEXTURES_PATH)["sheets"]
        trim = sheets["trim_castle"]
        u, v0 = G.strip_uv(trim, "plinth", 0.0, 0.0)
        _, v1 = G.strip_uv(trim, "plinth", 0.0, 0.6)
        self.assertEqual(u, 0.0)
        self.assertAlmostEqual(v0, 1 - (256 - 4) / 2048)  # profile bottom at the strip's bottom row
        self.assertAlmostEqual(v1, 1 - 4 / 2048)
        self.assertAlmostEqual(G.strip_uv(trim, "plinth", 9.6, 0.3)[0], 2.0)  # full-width strips wrap
        u_lo, _ = G.strip_uv(trim, "nosing_stone", 0.0, 0.0)
        u_hi, _ = G.strip_uv(trim, "nosing_stone", 99.0, 0.0)
        self.assertAlmostEqual(u_lo, (1024 + 4) / 2048)
        self.assertAlmostEqual(u_hi, (2048 - 4) / 2048)
        a = G.tile_uv(sheets["glass_sheet"], "light_lancet", 0.0, 0.0)
        b = G.tile_uv(sheets["glass_sheet"], "light_lancet", 1.0, 1.0)
        self.assertAlmostEqual(a[0], 4 / 1024)
        self.assertAlmostEqual(a[1], 4 / 1024)
        self.assertAlmostEqual(b[0], 252 / 1024)
        self.assertAlmostEqual(b[1], 1 - 4 / 1024)


class TextureSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = load(TEXTURES_PATH)

    def test_materials(self):
        mats = self.data["materials"]
        sheets = self.data["sheets"]
        self.assertEqual(self.data["version"], 1)
        textures = set()
        for name, m in mats.items():
            self.assertRegex(name, r"^castle_[a-z]+(_[a-z0-9]+)?$")
            self.assertIn(m["map"], ("world", "sheet", "atlas", "fit"), name)
            self.assertNotIn(m["texture"], textures, name)
            textures.add(m["texture"])
            w, h = m["px"]
            self.assertTrue(w & (w - 1) == 0 and h & (h - 1) == 0 and w >= 512, name)
            self.assertIn(m["milestone"], (1, 2, 3, 4, 5, 6, 7), name)  # castle M5b: the dungeon variants (6); M5c: roof, paving (7)
            if m["map"] == "world":
                self.assertIn(m["tile_m"], (G.STONE_TILE_M, G.CEILING_TILE_M), name)
            if m["map"] == "sheet" and m["milestone"] == 1:
                self.assertIn(m["texture"], sheets, name)
                self.assertEqual(sheets[m["texture"]]["px"], m["px"], name)
            if "family" in m:
                self.assertTrue(name.startswith(m["family"] + "_"), name)
            self.assertIn(m.get("alpha", "opaque"), ("opaque", "mask", "blend"), name)
            self.assertIn(m.get("tint"), (None, "sky", "sky_dome"), name)
            self.assertIn(m["ktx2"]["mode"], G.KTX2_MODES, name)
        self.assertEqual(mats["castle_props"]["emissive"], "props_atlas_e")
        self.assertEqual(mats["castle_glass"]["emissive"], "self")
        self.assertTrue(mats["castle_sky"]["unlit"])

    def test_corridor_textures_stay_inside_the_m1_memory_promise(self):
        # Plan M1: Entrance Hall GPU memory at Medium below the toon-pass build, whose
        # 7 resident kit cells each embedded a 1024 base + emissive atlas pair.
        mats = self.data["materials"]
        before = 7 * 2 * 1024 * 1024 * 4 * 4 / 3
        used = ["castle_wall_a", "castle_floor_square", "castle_ceiling", "castle_trim", "castle_glass", "castle_props",
                "castle_wood"]
        after = 0.0
        for name in used:
            w, h = mats[name]["px"]
            n = 2 if mats[name].get("emissive") not in (None, "self") else 1
            after += n * w * h * 4 * 4 / 3
        self.assertLess(after, before)

    def test_sheets_are_well_formed(self):
        for name, sh in self.data["sheets"].items():
            w, h = sh["px"]
            pad = sh["pad"]
            rects = []
            for sname, st in sh["strips"].items():
                top, bottom = st["rows"]
                c0, c1 = st.get("cols", [0, w])
                self.assertTrue(0 <= top < bottom <= h and 0 <= c0 < c1 <= w, (name, sname))
                self.assertGreater(bottom - top, 2 * pad, (name, sname))
                self.assertGreater(st["metres"], 0)
                self.assertGreater(st["u_m"], 0)
                rects.append((sname, c0, top, c1, bottom))
            for tname, (x, y, tw, th) in sh["tiles"].items():
                self.assertTrue(x >= 0 and y >= 0 and x + tw <= w and y + th <= h, (name, tname))
                self.assertGreater(min(tw, th), 2 * pad, (name, tname))
                rects.append((tname, x, y, x + tw, y + th))
            for x, y, rw, rh in sh.get("reserved", []):
                rects.append(("reserved", x, y, x + rw, y + rh))
            for i, a in enumerate(rects):
                for b in rects[i + 1:]:
                    overlap = a[1] < b[3] and b[1] < a[3] and a[2] < b[4] and b[2] < a[4]
                    self.assertFalse(overlap, (name, a[0], b[0]))
            names = list(sh["strips"]) + list(sh["tiles"])
            self.assertEqual(len(names), len(set(names)), name)

    def test_trim_strips_match_the_mouldings_and_densities(self):
        trim = self.data["sheets"]["trim_castle"]
        self.assertEqual(trim["px"], [2048, 2048])
        self.assertEqual(trim["pad"], 4)
        rows = sorted(st["rows"] for st in trim["strips"].values() if "cols" not in st or st["cols"][0] == 0)
        rows.append([1792, 1920])  # the fit tiles' band
        rows.sort()
        self.assertEqual(rows[0][0], 0)
        self.assertEqual(rows[-1][1], 2048)
        for a, b in zip(rows, rows[1:]):
            self.assertEqual(a[1], b[0])  # no gaps: the whole sheet is used
        for m in G.MOULDINGS.values():
            self.assertAlmostEqual(trim["strips"][m.strip]["metres"], m.height, msg=m.name)
        for sname, st in trim["strips"].items():
            px_v = (st["rows"][1] - st["rows"][0] - 2 * trim["pad"]) / st["metres"]
            cols = st.get("cols", [0, 2048])
            px_u = (cols[1] - cols[0]) / st["u_m"]
            if st.get("lod"):
                continue
            lo, hi = G.TRIM_PX_PER_M
            self.assertTrue(lo <= px_v <= hi, (sname, px_v))
            self.assertTrue(lo <= px_u <= hi, (sname, px_u))
        rim = trim["strips"]["arch_rim"]
        self.assertAlmostEqual(rim["u_m"] / rim["joint_m"], round(rim["u_m"] / rim["joint_m"]))

    def test_toon_rows_will_be_looked_up_by_name(self):
        # The client looks world ramps up by exact name, then the longest `*` prefix:
        # castle_* rows are the Client's to add; until then names fall to `default`.
        toon = load(TOON_PATH)
        self.assertIn("default", toon)
        for name in self.data["materials"]:
            self.assertNotIn("|", name)

    def test_props_slots_are_kit_tiles(self):
        slots = PA.extra_slots(TEXTURES_PATH)
        for tile, colour in slots:
            self.assertIn(tile, kit.TILES)
            self.assertRegex(colour, r"^(#[0-9A-F]{6}|-|@[a-z][a-z0-9_]*)$")


class PropsAtlasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cells, cls.atlas = PA.build()

    def test_same_table_as_the_kit_and_one_shared_size(self):
        cells, atlas = kit.assemble(kit.load_kit(PROPS_PATH))
        base = atlas.slots
        for key, index in base.items():
            self.assertEqual(self.atlas.slots[key], index)
        self.assertLessEqual(self.atlas.size, PA.MAX_PX)
        self.assertEqual(sorted(cells), sorted(self.cells))
        for cid in cells:
            self.assertEqual(cells[cid].uvs, self.cells[cid].uvs, cid)

    def test_key_is_stable_and_lookups_refuse_new_slots(self):
        _, again = PA.build()
        self.assertEqual(self.atlas.key(), again.key())
        self.assertRegex(self.atlas.key(), r"^[0-9a-f]{12}$")
        (tile, colour), index = next(iter(self.atlas.slots.items()))
        self.assertEqual(self.atlas.index(tile, colour if colour != "-" else "#123456"), index)
        with self.assertRaises(KeyError):
            self.atlas.index("glow", "#010203")
        with self.assertRaises(KeyError):
            self.atlas.index("no_such_tile")
        u0, v0, w, h = self.atlas.rect(tile, colour if colour != "-" else "#123456")
        self.assertTrue(0 <= u0 < 1 and 0 <= v0 < 1 and 0 < w < 1 and 0 < h < 1)

    def test_extra_slots_append_without_moving_any(self):
        data = load(TEXTURES_PATH)
        data["props_slots"] = data["props_slots"] + [["glow", "#FFD27A"], ["metal", "#3A3530"]]
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "castle_textures.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            cells, atlas = PA.build(PROPS_PATH, path)
        n = len(self.atlas.slots)
        for key, index in self.atlas.slots.items():
            self.assertEqual(atlas.slots[key], index)
        self.assertEqual(atlas.index("glow", "#ffd27a"), n)
        self.assertEqual(atlas.index("metal", "#3A3530"), n + 1)
        self.assertNotEqual(atlas.key(), self.atlas.key())
        for cid in cells:
            self.assertEqual(cells[cid].uvs, self.cells[cid].uvs, cid)


class WallRunTests(unittest.TestCase):
    def test_runs_split_at_doors_and_name_their_side(self):
        lay = layout([
            surf("a", 0, 0, 20, 10),
            surf("door", 20, 3, 22, 8, kind="door", ceiling=6.0),
            surf("b", 22, 0, 32, 10, cell="d"),
        ])
        runs = G.wall_runs(lay, lay.by_id("a"))
        ids = [r.id for r in runs]
        self.assertEqual(ids, ["a:n0", "a:s0", "a:w0", "a:e0", "a:e1"])
        e0, e1 = runs[3], runs[4]
        self.assertEqual((e0.axis, e0.c, e0.sign, e0.t0, e0.t1), ("x", 20.0, -1.0, 0.0, 3.0))
        self.assertEqual((e1.t0, e1.t1), (8.0, 10.0))
        n0 = runs[0]
        self.assertEqual((n0.axis, n0.c, n0.sign, n0.floor, n0.top), ("z", 0.0, 1.0, 0.0, 8.4))
        self.assertEqual(G.run_point(n0, 5.0, 1.0, 0.5), (5.0, 1.0, -0.5))
        joins = G.contacts(lay, lay.by_id("a"))
        self.assertEqual([(j.other, j.side, j.t0, j.t1, j.is_door) for j in joins], [("door", "e", 3.0, 8.0, True)])
        self.assertEqual(G.wall_runs(lay, lay.by_id("door")), [])

    def test_wall_mass_sees_rooms_behind_and_the_outside(self):
        lay = layout([
            surf("a", 0, 0, 20, 10),
            surf("near", 0, 12, 8, 20),  # 2 m behind the south wall, x 0..8
            surf("yard", 12, 11, 20, 20, kind="courtyard", ceiling=None),
        ])
        s0 = next(r for r in G.wall_runs(lay, lay.by_id("a")) if r.side == "s")
        m = G.wall_mass(lay, s0, 2.0, 4.0, 2.4, 7.4)
        self.assertEqual((m.behind, m.outside), (("near",), False))
        self.assertAlmostEqual(m.thickness, 0.5)  # half the 1 m gap to the courtyard
        clear = G.wall_mass(lay, s0, 14.0, 16.0, 2.4, 7.4)
        self.assertTrue(clear.outside)  # a courtyard is outside
        lone = layout([surf("a", 0, 0, 20, 10)])
        n0 = G.wall_runs(lone, lone.by_id("a"))[0]
        self.assertEqual(G.wall_mass(lone, n0, 0, 20, 0, 8.4), G.WallMass(G.EXTERIOR_SHELL, (), True))

    def test_railing_edges_have_no_wall(self):
        # A floor ending at a drop to a lower floor: a railing, no wall (castle kit M2).
        lay = layout([
            surf("a", 0, 0, 20, 10, ceiling=12.0),
            surf("pit", 0, 10, 20, 16, y=-3.0, ceiling=15.0, cell="p"),
        ])
        pieces = G.planner_pieces(lay)
        runs = {r.side: r for r in G.wall_runs(lay, lay.by_id("a"))}
        self.assertEqual(G.walled_spans(pieces, runs["s"], "full"), [])
        self.assertEqual(G.walled_spans(pieces, runs["n"], "full"), [(0.0, 20.0)])
        n = runs["n"]
        self.assertTrue(G.face_covered(pieces, "a", n.axis, n.c, n.sign, 2.0, 6.0, 0.0, 11.0))
        self.assertFalse(G.face_covered(pieces, "a", n.axis, n.c, n.sign, 2.0, 6.0, 0.0, 12.5))
        s = runs["s"]
        self.assertFalse(G.face_covered(pieces, "a", s.axis, s.c, s.sign, 2.0, 6.0, 2.4, 7.4))


class CastleKitDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = L.load_layout(LAYOUT_PATH)
        cls.data = load(KIT_PATH)
        cls.plan, cls.rep = S.resolve(cls.data, cls.layout, PROPS_PATH)

    def errors(self, data):
        _, rep = S.resolve(data, self.layout, PROPS_PATH)
        return rep.errors

    def assertError(self, data, fragment):
        errs = self.errors(data)
        self.assertTrue(any(fragment in e for e in errs), f"no error containing {fragment!r} in {errs}")

    def test_real_data_is_clean(self):
        self.assertEqual(self.rep.errors, [])
        self.assertEqual(self.rep.warnings, [])
        self.assertEqual(sorted(self.plan.cells), sorted(KITTED))
        self.assertEqual(sorted(self.plan.planned), sorted(PLANNED))
        self.assertEqual(S.kitted_cells(KIT_PATH), sorted(KITTED))
        self.assertEqual(S.load(KIT_PATH).cells.keys(), self.plan.cells.keys())

    def test_test_bed_rhythm(self):
        gf, ff = self.plan.cells["gf_corridor_west"], self.plan.cells["ff_corridor_north"]
        bays = {rid: b.n for rid, b in {**gf.bays, **ff.bays}.items()}
        self.assertEqual(bays["gf_corridor_west:n0"], 4)
        self.assertEqual(bays["gf_corridor_west:s0"], 4)
        # The Trophy Cabinet Stair's door (x 1..3, on the old line between bays 4 and 5)
        # splits the north wall: five bays, then the classroom doors' six as before.
        self.assertEqual((bays["ff_corridor_north:n0"], bays["ff_corridor_north:n1"]), (5, 6))
        self.assertEqual(bays["ff_corridor_north:s0"], 7)
        self.assertEqual(bays["ff_corridor_north:s1"], 3)
        # Castle M5a N7: the Long Gallery arch splits the east end wall into two one-bay runs.
        self.assertEqual((bays["ff_corridor_north:e0"], bays["ff_corridor_north:e1"]), (1, 1))
        arches = {(o.cell, o.door): o.piece for o in gf.openings + ff.openings if o.kind == "arch"}
        self.assertEqual(arches, {
            ("gf_corridor_west", "eh_west_door"): "kit_stone_arch_5x6",
            ("gf_corridor_west", "gs_door_l0"): "kit_stone_arch_5x6",
            ("ff_corridor_north", "gs_door_l1"): "kit_stone_arch_5x6",
            ("ff_corridor_north", "ff_marble_arch"): "kit_stone_arch_8x7",
            ("ff_corridor_north", "ff_gallery_arch"): "kit_stone_arch_5x6",
            ("ff_corridor_north", "sp_panel_door_corridor"): "kit_stone_arch_2x3.5",
        })
        marble = next(o for o in ff.openings if o.door == "ff_marble_arch")
        self.assertEqual(marble.reveal, 1.5)  # the door surface is this cell's: its passage less the hall's 0.5 m
        west = next(o for o in gf.openings if o.door == "eh_west_door")
        self.assertEqual(west.reveal, G.ARCH_REVEAL)
        self.assertEqual(ff.replaces, ["classroom_door_2", "classroom_door_3"])
        blind = sorted(o.centre for o in ff.openings if o.kind == "blind_door")
        # The History of Magic door at bay 7 (across from the Marble Staircase arch).
        for centre, landmark in zip(blind, (6.5, 18.5, 29.5)):
            self.assertLess(abs(centre - landmark), 1.5)
        self.assertEqual(len(gf.shafts), 6)
        # Castle M5a N7: one fewer on the split east wall; one fewer on the split north wall.
        self.assertEqual(len(ff.shafts), 17)
        self.assertEqual(len(gf.lanterns) + len(ff.lanterns), 10)  # One fewer on the split north wall
        self.assertEqual(gf.hero, [])
        # The Transfiguration class door's plaque over the blind door near classroom_door_2.
        # The History of Magic and Defence plaques over the doors of bays 7 and 9.
        # The plain oak panel over the hidden passage's door (x 1..3) first.
        self.assertEqual([(h.id, h.piece) for h in ff.hero], [("entrance_sp_panel_door_corridor", "kit_entrance_panel"),
                                                              ("transfiguration_plaque", "kit_class_plaque"),
                                                              ("history_plaque", "kit_class_plaque"),
                                                              ("defence_plaque", "kit_class_plaque")])
        for cp in (gf, ff):
            self.assertEqual(cp.shaft, "plain")
            for sh in cp.shafts:
                self.assertEqual(sh.piece, "kit_stone_shaft_8.4_plain")

    def test_test_bed_fits_the_corridor_draw_budget(self):
        # castle_wall, castle_floor, castle_ceiling, castle_trim, castle_props, + glass for
        # windows, + wood for doors: one mesh per material.
        for cid, cp in self.plan.cells.items():
            kinds = {o.kind for o in cp.openings}
            draws = 5 + ("window" in kinds) + ("blind_door" in kinds or any(o.leaves for o in cp.openings))
            self.assertLessEqual(draws, G.BUDGETS[cp.budget].draws, cid)

    def test_openings_stay_in_their_bays_below_the_cornice(self):
        for cp in list(self.plan.cells.values()) + list(self.plan.planned.values()):
            runs = {r.id: r for r in cp.runs}
            for o in cp.openings:
                if o.kind == "arch" or o.over:
                    continue
                r = runs[o.run]
                edges = cp.lines.edges() if o.line_bay else cp.bays[r.id].bounds(r.t0)
                lo, hi = edges[o.bay:o.bay + 2]
                self.assertGreaterEqual(o.t0, lo + G.OPENING_MARGIN - 1e-6, o.id)
                self.assertLessEqual(o.t1, hi - G.OPENING_MARGIN + 1e-6, o.id)
                self.assertLessEqual(o.y1, r.top - G.CORNICE.height + 1e-6, o.id)
                for sh in cp.shafts:
                    if sh.run == r.id:
                        self.assertFalse(o.t0 < sh.t < o.t1, (o.id, sh.id))

    def test_resolution_is_deterministic(self):
        again, _ = S.resolve(copy.deepcopy(self.data), self.layout, PROPS_PATH)
        for cid, cp in {**self.plan.cells, **self.plan.planned}.items():
            other = {**again.cells, **again.planned}[cid]
            self.assertEqual(other.openings, cp.openings)
            self.assertEqual(other.shafts, cp.shafts)
            self.assertEqual(other.lanterns, cp.lanterns)
            self.assertEqual(other.hero, cp.hero)

    def test_camera_max(self):
        p = self.plan
        self.assertEqual((p.camera_default, p.camera_low_cap), (16.0, 18.0))
        for cid in ("entrance_hall", "great_hall", "grand_staircase", "grand_staircase_upper", "courtyard"):
            self.assertEqual(p.camera_max(cid), 24.0)
        for house in "abcd":
            self.assertEqual(p.camera_max(f"common_room_house_{house}"), 20.0)
        self.assertEqual(p.camera_max("gf_corridor_west"), 16.0)

    def test_every_door_edge_needs_its_arch(self):
        d = copy.deepcopy(self.data)
        d["cells"]["gf_corridor_west"]["arches"].pop()
        self.assertError(d, "no arch for door 'gs_door_l0'")
        d = copy.deepcopy(self.data)
        d["cells"]["ff_corridor_north"]["arches"][1]["piece"] = "kit_stone_arch_8x10"
        self.assertError(d, "piece must be 'kit_stone_arch_8x7'")
        d = copy.deepcopy(self.data)
        d["cells"]["gf_corridor_west"]["arches"].append(
            {"door": "gh_doors", "surface": "gf_corridor_west", "piece": "kit_stone_arch_6x9"})
        self.assertError(d, "does not join surface")

    def test_windows_only_open_outside(self):
        d = copy.deepcopy(self.data)
        ops = d["cells"]["ff_corridor_north"]["openings"]
        ops[4]["bays"] = [4, 6]  # niches keep 4 and 6 ...
        ops.append({"kind": "window", "piece": "kit_window_lancet_1.6x5", "run": "ff_corridor_north:s0", "bays": [5],
                    "sill": 2.4})  # ... and a window where the Entrance Hall is behind the wall
        self.assertError(d, "would open into")

    def test_opening_errors(self):
        def with_opening(**kw):
            d = copy.deepcopy(self.data)
            op = {"kind": "window", "piece": "kit_window_lancet_1.6x5", "run": "gf_corridor_west:s0", "bays": [0],
                  "sill": 2.4}
            op.update(kw)
            d["cells"]["gf_corridor_west"]["openings"] = [op]
            return d

        self.assertEqual(self.errors(with_opening()), [])
        self.assertError(with_opening(run="gf_corridor_west:x0"), "unknown run")
        self.assertError(with_opening(sill=2.5), "multiple of the 0.6 m course")
        self.assertError(with_opening(sill=3.0), "runs into the cornice")
        self.assertError(with_opening(bays=[4]), "has bays 0..3")
        self.assertError(with_opening(piece="kit_niche_2x3.5"), "must be a window piece")
        self.assertError(with_opening(kind="door"), "kind must be one of")
        self.assertError(with_opening(t=-24.75), "one of bays, t or line_bays")
        t_only = with_opening(t=-24.5)
        del t_only["cells"]["gf_corridor_west"]["openings"][0]["bays"]
        self.assertEqual(self.errors(t_only), [])
        off_grid = copy.deepcopy(t_only)
        off_grid["cells"]["gf_corridor_west"]["openings"][0]["t"] = -24.75
        self.assertError(off_grid, "0.5 m plan grid")
        near_shaft = copy.deepcopy(t_only)
        near_shaft["cells"]["gf_corridor_west"]["openings"][0]["t"] = -22.0
        self.assertError(near_shaft, "does not fit bay")
        self.assertError(with_opening(kind="blind_door", piece="kit_door_blind_3x4.5"), "sill 0")
        twice = with_opening()
        twice["cells"]["gf_corridor_west"]["openings"].append(dict(twice["cells"]["gf_corridor_west"]["openings"][0]))
        self.assertError(twice, "overlaps")
        self.assertError(with_opening(colour="red"), "unknown field 'colour'")

    def test_props_and_tells_are_respected(self):
        d = copy.deepcopy(self.data)
        d["cells"]["ff_corridor_north"]["replaces"] = ["classroom_door_3"]
        self.assertError(d, "cuts into prop classroom_door_2")
        d = copy.deepcopy(self.data)
        d["cells"]["ff_corridor_north"]["replaces"].append("no_such_prop")
        self.assertError(d, "'no_such_prop' is not a prop or kit entry")
        d = copy.deepcopy(self.data)
        d["cells"]["ff_corridor_north"]["openings"][7]["bays"] = [1, 3]  # N1's windows
        d["cells"]["ff_corridor_north"]["openings"].append(
            {"kind": "niche", "piece": "kit_niche_2x3.5", "run": "ff_corridor_north:n1", "t": 38.0, "sill": 0.6})
        errs = self.errors(d)
        self.assertTrue(any("does not fit bay" in e or "covers kit entry tell_ff_corridor_window" in e for e in errs), errs)

    def test_cell_level_errors(self):
        d = copy.deepcopy(self.data)
        d["cells"]["nowhere"] = {}
        self.assertError(d, "unknown cell 'nowhere'")
        d = copy.deepcopy(self.data)
        d["cells"]["gf_corridor_west"]["materials"]["castle_wall"] = "castle_floor_long"
        self.assertError(d, "materials.castle_wall")
        d = copy.deepcopy(self.data)
        del d["cells"]["gf_corridor_west"]["materials"]["castle_floor"]
        self.assertError(d, "materials.castle_floor is required")
        d = copy.deepcopy(self.data)
        d["cells"]["gf_corridor_west"]["budget"] = "palace"
        self.assertError(d, "budget must be one of")
        d = copy.deepcopy(self.data)
        d["cells"]["gf_corridor_west"]["lanterns"]["y"] = 2.6
        self.assertError(d, "lanterns.y")
        d = copy.deepcopy(self.data)
        d["cells"]["gf_corridor_west"]["hero"] = [{"id": "x", "piece": "Kit_Bad", "pos": [0, 0, 0], "yaw": 0}]
        self.assertError(d, "breaks the naming rule")
        d = copy.deepcopy(self.data)
        d["cells"]["gf_corridor_west"]["runs"] = {"gf_corridor_west:n0": {"bays": 5}}
        plan, rep = S.resolve(d, self.layout, PROPS_PATH)
        self.assertEqual(rep.errors, [])
        self.assertEqual(plan.cells["gf_corridor_west"].bays["gf_corridor_west:n0"].n, 5)
        d["cells"]["gf_corridor_west"]["runs"] = {"gf_corridor_west:q9": {"bays": 2}}
        self.assertError(d, "unknown run 'gf_corridor_west:q9'")
        d = copy.deepcopy(self.data)
        d["extra"] = 1
        self.assertError(d, "unknown top-level field 'extra'")
        d = copy.deepcopy(self.data)
        d["camera_max"]["cells"]["great_hall"] = 60
        self.assertError(d, "camera_max.great_hall")
        d = copy.deepcopy(self.data)
        d["camera_max"]["default"] = 20
        self.assertError(d, "camera_max.default")

    def test_kitted_cells_are_castle_cells(self):
        instances = os.path.join(REPO, "data", "world", "instances")
        inst_cells = set()
        for name in os.listdir(instances):
            if name.endswith(".json"):
                inst_cells |= {s["cell"] for s in load(os.path.join(instances, name)).get("surfaces", [])}
        self.assertFalse(set(self.plan.cells) & inst_cells)


class EntranceHallTests(unittest.TestCase):
    """Castle kit M2: the Entrance Hall's placements (data/world/castle_kit.json,
    "build": false until its pieces exist) and the schema rules M2 adds."""

    @classmethod
    def setUpClass(cls):
        cls.layout = L.load_layout(LAYOUT_PATH)
        cls.data = load(KIT_PATH)
        plan, rep = S.resolve(cls.data, cls.layout, PROPS_PATH)
        assert rep.ok, rep.errors
        cls.eh = plan.cells["entrance_hall"]
        cls.built = plan
        planned = copy.deepcopy(cls.data)
        planned["cells"]["entrance_hall"]["build"] = False
        cls.planned, prep = S.resolve(planned, cls.layout, PROPS_PATH)
        assert prep.ok, prep.errors

    def errors(self, data):
        _, rep = S.resolve(data, self.layout, PROPS_PATH)
        return rep.errors

    def assertError(self, data, fragment):
        errs = self.errors(data)
        self.assertTrue(any(fragment in e for e in errs), f"no error containing {fragment!r} in {errs}")

    def eh_data(self):
        return copy.deepcopy(self.data)

    def test_cell(self):
        eh = self.eh
        self.assertEqual((eh.budget, eh.build, eh.springing, eh.shaft), ("entrance_hall", True, 14.0, "plain"))
        self.assertEqual(eh.materials, {"castle_wall": "castle_wall_b", "castle_floor": "castle_floor_long"})
        self.assertEqual(self.planned.planned["entrance_hall"].build, False)
        self.assertEqual(eh.replaces, ["owl_exchange_counter"])

    def test_arches_at_every_door_edge(self):
        arches = {o.door: o for o in self.eh.openings if o.kind == "arch"}
        self.assertEqual({d: o.piece for d, o in arches.items()}, {
            "eh_front_doors": "kit_stone_arch_8x10",
            "gh_doors": "kit_stone_arch_6x9",
            "eh_west_door": "kit_stone_arch_5x6",
            "eh_north_door": "kit_stone_arch_3x4.5",  # castle M5a N1: the Undercroft Door
            "eh_owl_gate": "kit_stone_arch_3x4.5",  # castle M5c N5: the Owl Gate
            "ff_marble_arch": "kit_stone_arch_8x7",
        })
        front, gh = arches["eh_front_doors"], arches["gh_doors"]
        # Castle M5c: the courtyard is kitted, so its arch takes ARCH_REVEAL of the 2 m passage (as every door
        # between two kitted cells); the hall's reveal is the rest.
        reveal = 2.0 - (G.ARCH_REVEAL if "courtyard" in KITTED else 0.0)
        self.assertEqual((front.leaves, front.leaf_mode, front.leaf_width, front.reveal), (True, "face", 4.0, reveal))
        self.assertEqual((gh.leaves, gh.leaf_mode, gh.leaf_width, gh.reveal), (True, "face", 3.0, G.ARCH_REVEAL))
        self.assertFalse(arches["eh_west_door"].leaves or arches["ff_marble_arch"].leaves or arches["eh_north_door"].leaves
                         or arches["eh_owl_gate"].leaves)
        # The Marble Arch stands on the stair's level top edge.
        marble = arches["ff_marble_arch"]
        self.assertEqual((marble.surface, marble.y0, marble.y1, marble.reveal), ("eh_marble_staircase", 9.0, 16.0, 0.5))

    def test_passages_are_lined_once_when_both_sides_are_kitted(self):
        ops = [o for cp in self.built.cells.values() for o in cp.openings if o.kind == "arch"]
        depth = {"eh_west_door": 2.0, "ff_marble_arch": 2.0}
        for door, d in depth.items():
            sides = [o.reveal for o in ops if o.door == door]
            self.assertEqual(len(sides), 2, door)
            self.assertAlmostEqual(sum(sides), d, msg=door)
        # With the hall still greybox, the corridor lines the Marble Arch's whole passage (M1).
        marble = next(o for o in self.planned.cells["ff_corridor_north"].openings if o.door == "ff_marble_arch")
        self.assertEqual(marble.reveal, 2.0)

    def test_windows(self):
        wins = [o for o in self.eh.openings if o.kind == "window"]
        lancets = [o for o in wins if o.piece == "kit_window_lancet_2.6x7"]
        self.assertEqual(len(lancets), 4)
        for o in lancets:
            self.assertEqual((o.y0, o.y1), (9.0, 16.0), o.id)
        tracery = [o for o in wins if o.piece == "kit_window_tracery_8x8"]
        self.assertEqual(len(tracery), 1)
        t = tracery[0]
        self.assertEqual((t.over, t.door, t.run, t.t0, t.t1, t.y0, t.y1), (True, "eh_front_doors", None, 12.0, 20.0, 10.8, 18.8))
        for o in wins:
            self.assertIsNotNone(o.wall)

    def test_armour_in_niches(self):
        niches = [o for o in self.eh.openings if o.kind == "niche"]
        suits = [h for h in self.eh.hero if h.piece == "kit_armour_suit"]
        # Castle M5a N1: the north-west niche and its suit gave way to eh_north_door.
        self.assertEqual((len(niches), len(suits)), (5, 5))
        for h in suits:
            x, y, z = h.pos
            home = [o for o in niches if abs(o.centre - (x if o.axis == "z" else z)) < 1e-6
                    and abs((((z if o.axis == "z" else x) - o.c) * o.sign) + 0.35) < 1e-6]
            self.assertEqual(len(home), 1, h.id)
            self.assertAlmostEqual(y, home[0].y0, msg=h.id)

    def test_hero_placements(self):
        by = {}
        for h in self.eh.hero:
            by.setdefault(h.piece, []).append(h)
        self.assertEqual(len(by["kit_vault_lierne_bay_32x10"]), 4)
        self.assertEqual(sorted(h.pos[2] for h in by["kit_vault_lierne_bay_32x10"]), [5.0, 15.0, 25.0, 35.0])
        self.assertEqual(len(by["kit_vault_fan_12x10"]), 1)
        self.assertEqual(len(by["kit_newel_lamp"]), 4)
        counter = by["kit_owl_counter"][0]
        self.assertEqual((counter.pos, dict(counter.args)), ((0.0, 0.0, 30.0), {"width": 12.0}))
        self.assertEqual(len(by["kit_lantern_hanging"]), 2)
        for h in by["kit_lantern_hanging"]:
            # Over the Exchange court, the lantern's bottom well above 2.4 m.
            self.assertTrue(0 <= h.pos[0] <= 12 and 22 <= h.pos[2] <= 38, h.id)
            self.assertGreater(h.pos[1] - dict(h.args)["drop"] - 1.0, G.OVERHEAD_CLEARANCE)
        self.assertEqual(len(by["kit_owl_perch"]), 2)
        # The Owl Exchange interactable's reach ring stays clear of every hero piece but the counter.
        ox, oz, reach = 3.5, 30.0, 3.5
        for h in self.eh.hero:
            if h.piece in ("kit_owl_counter", "kit_vault_lierne_bay_32x10", "kit_lantern_hanging"):
                continue
            self.assertGreater((h.pos[0] - ox) ** 2 + (h.pos[2] - oz) ** 2, reach ** 2, h.id)

    def test_shafts_and_lanterns(self):
        eh = self.eh
        self.assertEqual(sorted((sh.run, sh.t) for sh in eh.shafts), [
            ("eh_floor_east:e0", 5.0), ("eh_floor_east:e0", 10.0), ("eh_floor_east:e1", 30.0),
            ("eh_floor_east:e1", 35.0), ("eh_floor_west:w1", 15.0), ("eh_floor_west:w1", 20.0)])
        for sh in eh.shafts:
            self.assertEqual((sh.piece, sh.y0, sh.y1), ("kit_stone_shaft_14_plain", 0.0, 14.0))
            if sh.run.startswith("eh_floor_west"):
                self.assertFalse(24.0 - 0.5 < sh.t < 36.0 + 0.5, sh.id)  # none behind the counter
        self.assertEqual(len(eh.lanterns), 6)
        # The stairwells' railing edges have no wall: no shafts, no openings.
        for rid in ("eh_floor_ws:n0", "eh_floor_es:n0"):
            self.assertEqual(eh.walled[rid], [], rid)
            self.assertFalse([o for o in eh.openings if o.run == rid], rid)

    def test_openings_need_a_wall(self):
        d = self.eh_data()
        d["cells"]["entrance_hall"]["openings"].append(
            {"kind": "niche", "piece": "kit_niche_2x3.5", "run": "eh_floor_ws:n0", "bays": [0], "sill": 0.6})
        self.assertError(d, "no wall behind bay 0 of eh_floor_ws:n0")
        d = self.eh_data()
        d["cells"]["entrance_hall"]["openings"].append(
            {"kind": "window", "piece": "kit_window_lancet_1.6x5", "run": "eh_floor_es:n0", "bays": [0], "sill": 2.4})
        self.assertError(d, "no wall behind")

    def test_windows_over_doors(self):
        def over(**kw):
            d = self.eh_data()
            ops = d["cells"]["entrance_hall"]["openings"]
            op = next(o for o in ops if o.get("over"))
            op.update(kw)
            return d

        self.assertError(over(sill=10.2), "must be at least 10.6 m")
        self.assertError(over(sill=12.0), "runs into the cornice")
        self.assertError(over(over="gs_door_l0"), "must be a door joining exactly one surface")
        self.assertError(over(run="eh_floor_south:s0"), "takes no run, bays or t")
        self.assertError(over(kind="niche", piece="kit_niche_2x3.5"), "only windows stand over doors")
        wide = self.eh_data()
        next(o for o in wide["cells"]["entrance_hall"]["openings"] if o.get("over"))["over"] = "eh_west_door"
        self.assertError(wide, "wider than eh_west_door")

    def test_open_leaf_rule(self):
        d = self.eh_data()
        d["cells"]["entrance_hall"]["runs"]["eh_floor_east:e0"] = {"bounds": [5, 10, 15]}
        self.assertError(d, "would cover eh_floor_east:e0:shaft3")
        d = self.eh_data()
        d["cells"]["entrance_hall"]["openings"].append(
            {"kind": "niche", "piece": "kit_niche_2x3.5", "run": "eh_floor_east:e1", "t": 25.5, "sill": 0.6})
        self.assertError(d, "would cover eh_floor_east:e1:niche@25.5")
        d = self.eh_data()
        next(a for a in d["cells"]["gf_corridor_west"]["arches"] if a["door"] == "eh_west_door")["leaves"] = True
        next(a for a in d["cells"]["entrance_hall"]["arches"] if a["door"] == "eh_west_door")["leaves"] = True
        self.assertError(d, "door eh_west_door: leaves on more than one side")

    def test_leaves_in_a_deep_reveal_or_on_a_wall_face(self):
        def kit(leaf_room_z):
            lay = layout([
                surf("a", 0, leaf_room_z[0], 20, leaf_room_z[1], cell="c"),
                surf("d3", 20, 2, 22, 5, kind="door", ceiling=4.5, cell="c"),
                surf("d5", 20, 12, 22, 17, kind="door", ceiling=6.0, cell="c"),
                surf("b", 22, -10, 32, 25, cell="e"),
            ])
            data = {"version": 1, "cells": {"c": {
                "budget": "corridor", "materials": {"castle_wall": "castle_wall_a", "castle_floor": "castle_floor_square"},
                "arches": [{"door": "d3", "surface": "a", "piece": "kit_stone_arch_3x4.5", "leaves": True},
                           {"door": "d5", "surface": "a", "piece": "kit_stone_arch_5x6", "leaves": True}],
                "openings": [], "lanterns": None}}}
            return lay, data

        lay, data = kit((-10, 25))
        plan, rep = S.resolve(data, lay, os.path.join(HERE, "no_props.json"))
        self.assertEqual(rep.errors, [])
        modes = {o.door: (o.leaf_mode, o.leaf_width) for o in plan.cells["c"].openings}
        self.assertEqual(modes, {"d3": ("reveal", 1.5), "d5": ("face", 2.5)})
        lay, data = kit((-10, 19))  # the 5 m door's south leaf (z 17.8..20.3) runs off the wall
        _, rep = S.resolve(data, lay, os.path.join(HERE, "no_props.json"))
        self.assertEqual(rep.errors, ["c: arch d5: open leaf at t 17.8..20.3 has no wall to lie against (from the floor to 6 m)"])

    def test_m2_cell_fields(self):
        def cell(**kw):
            d = self.eh_data()
            d["cells"]["entrance_hall"].update(kw)
            return d

        self.assertError(cell(springing=19.5), "springing must be")
        self.assertError(cell(springing=3.0), "springing must be")
        self.assertError(cell(shaft="fluted"), "shaft must be one of")
        self.assertError(cell(build="yes"), "build must be true or false")
        d = self.eh_data()
        d["cells"]["entrance_hall"]["runs"]["eh_floor_west:w1"] = {"bounds": [15.25]}
        self.assertError(d, "bounds must be increasing t")
        d["cells"]["entrance_hall"]["runs"]["eh_floor_west:w1"] = {"bounds": [11.5]}
        self.assertError(d, "every bay at least 1 m")
        d["cells"]["entrance_hall"]["runs"]["eh_floor_west:w1"] = {"bounds": [15], "bays": 3}
        self.assertError(d, "must be {\"bays\"")
        d = self.eh_data()
        d["cells"]["entrance_hall"]["shaft"] = "clustered"
        plan, rep = S.resolve(d, self.layout, PROPS_PATH)
        self.assertEqual(rep.errors, [])
        self.assertEqual({sh.piece for sh in plan.cells["entrance_hall"].shafts}, {"kit_stone_shaft_14"})


class TexturesStubTests(unittest.TestCase):
    def test_writes_a_valid_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = T.write_manifest(os.path.join(tmp, "textures"), {})
            data = load(path)
        self.assertEqual(data, {"version": 1, "materials": {}})


# =====================================================================================
# Castle kit M3 + M4 (docs/art/castle-kit.md)
# =====================================================================================


class M34GridTests(unittest.TestCase):
    def test_great_hall_numbers(self):
        self.assertEqual([34 + G.GH_BAY * k for k in range(9)], [34, 41.5, 49, 56.5, 64, 71.5, 79, 86.5, 94])
        self.assertLess(G.GH_CORBEL, G.GH_PLATE)
        self.assertLessEqual(G.GH_RIDGE, 29.0 - G.SKY_CEILING_CLEAR)
        lancet = G.OPENING_PIECES["kit_window_lancet_3.2x10"]
        self.assertLessEqual(G.GH_WINDOW_SILL + lancet.height, G.GH_PLATE - G.SKY_OPENING_CLEAR)
        self.assertAlmostEqual(G.GH_WINDOW_SILL / G.COURSE, round(G.GH_WINDOW_SILL / G.COURSE))
        self.assertGreaterEqual(G.GH_WINDOW_SILL, 3.2 + G.WAINSCOT_CLEAR)  # over the wainscot
        fire = G.OPENING_PIECES["kit_fireplace_hooded_4.4x3.6"]
        self.assertEqual((fire.kind, fire.depth), ("fireplace", 0.7))
        self.assertIn("fireplace", G.FLOOR_OPENINGS)
        self.assertGreaterEqual(G.FIREPLACE_HOOD[0], G.INTRUSION_HEIGHT)  # the hood stands proud only above 2.4 m
        self.assertLessEqual(G.FIREPLACE_HOOD_WIDTH, G.GH_BAY - G.SHAFT.height - 2 * 0.3)
        self.assertEqual(G.OPENING_PIECES["kit_window_end_9x12"].lights, 6)

    def test_shaft_none_and_budgets(self):
        self.assertIn("none", G.SHAFT_VARIANTS)
        with self.assertRaises(ValueError):
            G.shaft_piece(8.8, "none")
        self.assertEqual(G.shaft_piece(8.8), "kit_stone_shaft_8.8_plain")
        # Castle D1: 12 -> 13 and 20 -> 22.
        # 13 -> 19 (+ 4 hourglass sand nodes, + 2 for one house's cup banners).
        self.assertEqual(G.BUDGETS["great_hall"], G.Budget(19, (130_000, 190_000)))
        # + the three moving flights' swing nodes.
        self.assertEqual(G.BUDGETS["grand_staircase"], G.Budget(22 + G.P3C_GS_DRAWS_ADD, (250_000, 320_000), 180_000, 2))

    def test_candle_and_lod_rules(self):
        self.assertEqual(G.CANDLE_GROUPS_MAX, 4)
        self.assertTrue(G.CANDLE_LOD[0] <= G.LOD_CANDLES <= G.CANDLE_LOD[1])
        self.assertEqual(G.LOD_STOREY, G.STOREY)
        self.assertEqual(G.LOW_CANDLE_GROUPS, 1)
        self.assertEqual(G.CAMERA_MAX_LOW_CAP, 18.0)
        for g in ("l0", "l7", "candles_0", "candles_3"):
            self.assertRegex(g, G.GROUP_RE)
        for g in ("l10", "candles_4", "band", "L1"):
            self.assertNotRegex(g, G.GROUP_RE)
        self.assertEqual(G.node_name("great_hall", "castle_props"), "great_hall.castle_props")
        self.assertEqual(G.node_name("great_hall", "castle_props", "candles_2"), "great_hall.castle_props.candles_2")
        self.assertEqual(G.lod1_name("grand_staircase.castle_portraits_a.l1"), "grand_staircase.castle_portraits_a.l1_lod1")
        with self.assertRaises(ValueError):
            G.node_name("great_hall", "castle_props", "candles_9")

    def test_portrait_sheet_layout(self):
        self.assertEqual(G.PORTRAIT_CANVASES, 64)
        self.assertEqual(2 * G.PORTRAIT_CANVASES, 128)
        self.assertEqual(G.PORTRAIT_PAINT, 208)
        self.assertEqual(G.canvas_cell(0), (0, 0))
        self.assertEqual(G.canvas_cell(9), (256, 256))
        self.assertEqual(G.canvas_cell(63), (1792, 1792))
        with self.assertRaises(ValueError):
            G.canvas_cell(64)
        ranges = sorted(G.PORTRAIT_KINDS.values())
        self.assertEqual(ranges[0][0], 0)
        self.assertEqual(ranges[-1][1], G.PORTRAIT_CANVASES)
        for a, b in zip(ranges, ranges[1:]):
            self.assertEqual(a[1], b[0])
        sheet, index = G.GUARDIAN_CANVAS
        self.assertEqual((sheet, index), ("castle_portraits_b", 63))
        # Paint crops stay inside the painting, centred, with the frame's aspect.
        for aspect in (0.8, 1.0, 1.6):
            u0, v0 = G.canvas_paint_uv(9, 0.0, 0.0, aspect)
            u1, v1 = G.canvas_paint_uv(9, 1.0, 1.0, aspect)
            w, h = (u1 - u0) * 2048, (v1 - v0) * 2048
            self.assertAlmostEqual(w / h, aspect)
            self.assertLessEqual(max(w, h), G.PORTRAIT_PAINT + 1e-6)
            self.assertGreaterEqual(u0 * 2048, 256 + G.PORTRAIT_PAD + G.PORTRAIT_RING - 1e-6)
        mid = G.canvas_paint_uv(9, 0.5, 0.5, 0.8)
        self.assertAlmostEqual(mid[0] * 2048, 256 + 128)
        self.assertAlmostEqual((1 - mid[1]) * 2048, 256 + 128)
        # The ring: outer edge at the padding, inner edge at the painting; n is the top.
        n_out = G.canvas_ring_uv(0, "n", 0.0, 0.0)
        n_in = G.canvas_ring_uv(0, "n", 0.0, 1.0)
        self.assertAlmostEqual((1 - n_out[1]) * 2048, G.PORTRAIT_PAD)
        self.assertAlmostEqual((1 - n_in[1]) * 2048, G.PORTRAIT_PAD + G.PORTRAIT_RING)
        e_in = G.canvas_ring_uv(0, "e", 0.5, 1.0)
        self.assertAlmostEqual(e_in[0] * 2048, 256 - G.PORTRAIT_PAD - G.PORTRAIT_RING)
        full = (G.canvas_full_uv(0, 0, 0), G.canvas_full_uv(0, 1, 1))
        self.assertAlmostEqual(full[0][0] * 2048, G.PORTRAIT_PAD)
        self.assertAlmostEqual(full[1][0] * 2048, 256 - G.PORTRAIT_PAD)

    def test_portrait_frames_are_human_scale(self):
        weights = 0.0
        for w, h, shape, weight in G.PORTRAIT_FRAMES:
            self.assertIn(shape, G.PORTRAIT_SHAPES)
            self.assertTrue(0.8 <= w <= 3.0 and 1.0 <= h <= 4.0 or 0.8 <= h <= 3.0 and w <= 3.0, (w, h))
            self.assertLessEqual(h, G.PORTRAIT_Y[1] - G.PORTRAIT_Y[0] - 2 * G.PORTRAIT_EDGE)
            weights += weight
        self.assertGreater(weights, 0)
        self.assertLessEqual(G.PORTRAIT_PROUD, G.INTRUSION_MAX)
        self.assertLessEqual(G.PORTRAIT_Y[1], G.STOREY - G.SLAB - 0.9)  # under the next gallery's corbel table
        self.assertLessEqual(G.PORTRAIT_TRIS_LOD1, 2)

    def test_face_tags_name_palette_colours(self):
        self.assertEqual(G.palette_tag("metal", "iron"), "castle_props|kit:metal:@iron")
        self.assertEqual(G.parse_face_tag("castle_props|kit:metal:@iron").arg, "metal:@iron")
        G.parse_face_tag("castle_props|kit:metal:#3E4150")  # the props.json kit models' form stays
        for bad in ("castle_props|kit:metal:@Iron", "castle_props|kit:metal:@", "castle_props|kit:metal:iron"):
            with self.assertRaises(ValueError):
                G.parse_face_tag(bad)
        with self.assertRaises(ValueError):
            G.palette_tag("metal", "Iron")

    def test_face_walled_accepts_any_owner_and_sloped_feet(self):
        lay = layout([
            surf("a", 0, 0, 20, 10, ceiling=20.0),
            surf("b", 20, 0, 30, 10, ceiling=20.0),
            {"id": "st", "kind": "stair", "name": "st", "level": 0, "cell": "c", "min": [30, 0], "max": [32, 10], "y": 0,
             "y_top": 0.6, "rise": "+x", "ceiling": 20.0},
            surf("d", 32, 0, 40, 10, y=0.6, ceiling=19.4),
        ])
        pieces = G.planner_pieces(lay)
        self.assertTrue(G.face_walled(pieces, "z", 0.0, 1.0, 19.5, 20.5, 3.2, 12.0))  # across two runs
        self.assertTrue(G.face_walled(pieces, "z", 0.0, 1.0, 30.5, 31.5, 3.8, 12.0))  # over the steps
        self.assertFalse(G.face_walled(pieces, "z", 0.0, 1.0, 30.5, 31.5, 0.0, 12.0))  # not down to the floor there
        self.assertFalse(G.face_walled(pieces, "z", 0.0, 1.0, 39.5, 40.5, 3.2, 12.0))  # past the end


class PaletteRuleTests(unittest.TestCase):
    """The palette rule (castle kit M3): every stone, floor, trim, wood, cloth
    and metal colour comes from palette data; piece code writes none."""

    KIT_DIR = os.path.join(os.path.dirname(HERE), "castle_kit")

    def lint_files(self):
        out = []
        for d in G.COLOUR_LINT_DIRS:
            for name in sorted(os.listdir(os.path.join(self.KIT_DIR, d))):
                rel = f"{d}/{name}"
                if name.endswith(".py") and rel not in G.COLOUR_LINT_EXEMPT:
                    out.append(rel)
        return out + list(G.COLOUR_LINT_FILES)

    def test_no_colour_literals_in_piece_code(self):
        files = self.lint_files()
        self.assertIn("pieces/fixtures.py", files)
        self.assertIn("pieces/hall.py", files)
        self.assertIn("assemble.py", files)
        for rel in files:
            with open(os.path.join(self.KIT_DIR, rel), encoding="utf-8") as f:
                hits = G.colour_literals(f.read())
            self.assertEqual(hits, [], f"{rel} writes colour literals; name them in castle_textures.json palette")

    def test_the_lint_catches_a_literal(self):
        self.assertEqual(G.colour_literals('IRON = "castle_props|kit:metal:#3E4150"\nx = 1\n'),
                         [(1, 'IRON = "castle_props|kit:metal:#3E4150"')])
        self.assertEqual(G.colour_literals("tint = '#ffaa00'  # warm"), [(1, "tint = '#ffaa00'  # warm")])
        self.assertEqual(G.colour_literals("ref = '#1234567890'\nslot = '@iron'"), [])

    def test_pieces_use_named_slots_the_atlas_holds(self):
        from castle_kit import pieces as P

        _, atlas = PA.build(PROPS_PATH, TEXTURES_PATH)
        names = PA.palette(TEXTURES_PATH)
        for piece, params in P.CATALOGUE.items():
            p = P.build(piece, **params)
            for f in p.faces + (p.lod1 or []):
                ft = G.parse_face_tag(f.tag)
                if ft.mode != "kit":
                    continue
                tile, _, colour = ft.arg.partition(":")
                self.assertFalse(colour.startswith("#"), f"{piece}: {f.tag} (name the colour)")
                if colour:
                    self.assertIn(colour[1:], names, f"{piece}: {f.tag}")
                    atlas.index(tile, colour)  # a props_slots row holds it

    def test_palette_and_named_slots(self):
        data = load(TEXTURES_PATH)
        names = PA.palette(TEXTURES_PATH)
        self.assertGreater(len(names), 10)
        for name, colour in names.items():
            self.assertRegex(name, G.PALETTE_NAME_RE)
            self.assertRegex(colour, r"^#[0-9A-F]{6}$")
        for tile, colour in data["props_slots"]:
            if colour.startswith("@"):
                self.assertIn(colour[1:], names)
                self.assertTrue(kit.TILES[tile][2], tile)
        named = [tuple(s) for s in data["props_slots"] if s[1].startswith("@")]
        self.assertEqual(len(named), len(set(named)))
        # The glow colours stay inside the glow rule whatever the palette says (M2, 15.6).
        import paint

        for name in ("glow_lantern", "glow_candle"):
            r, g, b = (float(v) for v in paint.hex_rgb(names[name]))
            self.assertGreaterEqual(r, G.GLOW_BAND["r"][0], name)
            self.assertTrue(G.GLOW_BAND["g"][0] <= g <= G.GLOW_BAND["g"][1], name)
            self.assertTrue(G.GLOW_BAND["b"][0] <= b <= G.GLOW_BAND["b"][1], name)

    def test_named_slots_keep_their_place_when_the_palette_changes(self):
        _, atlas = PA.build(PROPS_PATH, TEXTURES_PATH)
        data = load(TEXTURES_PATH)
        data["palette"]["iron"] = "#112233"
        data["palette"]["gilt"] = data["palette"]["brass"]  # two names, one colour: still two slots
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "castle_textures.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            cells, again = PA.build(PROPS_PATH, path)
            base, _ = again.paint()
        self.assertEqual(again.slots, atlas.slots)  # no UV moves
        self.assertNotEqual(again.key(), atlas.key())  # the painted atlas changes
        self.assertNotEqual(again.index("metal", "@gilt"), again.index("metal", "@brass"))
        self.assertEqual(again.colour("@iron"), "#112233")
        i = again.index("metal", "@iron")
        g = max(4, again._atlas.grid)
        x, y = (i % g) * 64, (i // g) * 64
        mean = base[y + 8:y + 56, x + 8:x + 56, :3].reshape(-1, 3).mean(axis=0)
        self.assertLess(mean[0], mean[2])  # the new iron is blue-ish (0x11, 0x22, 0x33)

    def test_colour_0_is_grey(self):
        from castle_kit import assemble as A

        for ramp in ("wall", "wall_exterior", "wall_dungeon"):
            import palette

            c = palette.ramp_factor(ramp, 0.0, 0.0, 10.0)
            v = A.grey(c)
            self.assertTrue(min(c) - 1e-6 <= v <= max(c) + 1e-6, ramp)
        face = A.WFace([(0, 0, 0), (1, 0, 0), (1, 3, 0), (0, 3, 0)], "castle_wall_a", "world", None,
                       [(0, 0)] * 4, "base", ramp="wall")
        md = A.Builder._mesh("c.castle_wall_a", "castle_wall_a", [face], (0.0, 10.0))
        # Castle D1: the merge leaves COLOR_0 to the bake (castle_kit/shade.py), which
        # writes one grey x per corner from the faces kept in src_faces.
        self.assertIsNone(md.colors)
        self.assertEqual(md.src_faces, [face])
        from castle_kit import shade as SH

        g = [SH.gradient(face.ramp, p[1], (0.0, 10.0)) for p in face.pts]
        self.assertLess(g[0], 1.0)  # still a gentle value ramp at the foot
        self.assertEqual(g[0], G.SHADE_WALL_BOTTOM)


class GroupMergeTests(unittest.TestCase):
    """Castle kit M3 + M4 plumbing (assemble.py, greybox.py): named groups of
    one material, their LOD1 nodes and extras (docs/art/castle-kit.md)."""

    def test_groups_lods_and_extras(self):
        from castle_kit import assemble as A

        def face(mat, x, group=None, lod="both"):
            f = A.WFace([(x, 0, 0), (x + 1, 0, 0), (x + 1, 1, 0), (x, 1, 0)], mat, "kit", "metal", [(0, 0)] * 4, "p",
                        lod=lod)
            f.group = group
            return f

        faces = [face("castle_props", 0), face("castle_props", 2, "candles_0", "lod0"),
                 face("castle_props", 2, "candles_0", "lod1"), face("castle_portraits_a", 4, "l1", "lod0"),
                 face("castle_portraits_a", 4, "l1", "lod1"), face("castle_cloth", 6)]
        specs = {"candles_0": G.GroupSpec("candles_0", 40.0, (("amp", 0.06), ("kind", "bob"), ("period", 5.0),
                                                              ("phase", 0.0))),
                 "l1": G.GroupSpec("l1", G.LOD_STOREY)}
        b = A.Builder.__new__(A.Builder)
        meshes, lods, extras = b._merge("c", faces, [], specs)
        self.assertEqual(sorted(meshes), ["c.castle_cloth", "c.castle_portraits_a.l1", "c.castle_props",
                                          "c.castle_props.candles_0"])
        self.assertEqual({n: (base, d) for n, (_, base, d) in lods.items()}, {
            "c.castle_props.candles_0_lod1": ("c.castle_props.candles_0", 40.0),
            "c.castle_portraits_a.l1_lod1": ("c.castle_portraits_a.l1", G.LOD_STOREY),
        })
        self.assertEqual(extras["c.castle_props.candles_0"]["kit_anim"],
                         {"amp": 0.06, "kind": "bob", "period": 5.0, "phase": 0.0, "group": "candles_0"})
        self.assertEqual(sorted(extras), ["c.castle_props.candles_0"])
        # Without groups nothing changes (M1/M2 names).
        meshes, lods, extras = b._merge("c", [face("castle_trim", 0), face("castle_trim", 1, None, "lod1")], [], {})
        self.assertEqual(list(meshes), ["c.castle_trim"])
        self.assertEqual(list(lods), ["c.castle_trim_lod1"])
        self.assertEqual(extras, {})


class M3M4DataTests(unittest.TestCase):
    """The Great Hall, the Grand Staircase and the seventh-floor corridor in
    castle_kit.json (planned: "build": false until their pieces
    exist), and the schema rules M3 + M4 add."""

    @classmethod
    def setUpClass(cls):
        cls.layout = L.load_layout(LAYOUT_PATH)
        cls.data = load(KIT_PATH)
        cls.plan, cls.rep = S.resolve(cls.data, cls.layout, PROPS_PATH)
        assert cls.rep.ok, cls.rep.errors
        cls.cells = {**cls.plan.cells, **cls.plan.planned}
        cls.gh = cls.cells["great_hall"]

    def errors(self, data):
        _, rep = S.resolve(data, self.layout, PROPS_PATH)
        return rep.errors

    def assertError(self, data, fragment):
        errs = self.errors(data)
        self.assertTrue(any(fragment in e for e in errs), f"no error containing {fragment!r} in {errs}")

    def d(self):
        return copy.deepcopy(self.data)

    # -- the Great Hall ------------------------------------------------------------------

    def test_great_hall_cell(self):
        gh = self.gh
        self.assertEqual((gh.budget, gh.springing, gh.wainscot, gh.sky_plate, gh.sky_ridge), ("great_hall", 17.5, 3.2, 17.5,
                                                                                               28.5))
        self.assertEqual(gh.materials, {"castle_wall": "castle_wall_b", "castle_floor": "castle_floor_long"})
        self.assertEqual(gh.lines.at, (41.5, 49.0, 56.5, 64.0, 71.5, 79.0, 86.5, 94.0))
        self.assertEqual(gh.lines.ends, (34.0, 100.0))
        self.assertEqual(gh.shafts, [])
        self.assertEqual(len(gh.line_shafts), 16)
        for ls in gh.line_shafts:
            self.assertEqual(ls.y1, G.GH_CORBEL)
            self.assertGreaterEqual(ls.y0, 3.2)
            self.assertEqual(ls.corbel, "kit_stone_corbel_1.4")
        self.assertEqual({ls.run for ls in gh.line_shafts if ls.t == 94.0}, {None})  # over the dais steps

    def test_great_hall_openings(self):
        ops = [o for o in self.gh.openings if o.kind != "arch"]
        lancets = [o for o in ops if o.piece == "kit_window_lancet_3.2x10"]
        self.assertEqual(len(lancets), 15)  # 16 side bays less the fireplace bay
        self.assertEqual({o.y0 for o in lancets}, {6.6})
        self.assertEqual(sorted((o.sign > 0, o.bay) for o in lancets if o.sign > 0),
                         [(True, b) for b in (0, 1, 2, 3, 4, 6, 7)])
        fire = [o for o in ops if o.kind == "fireplace"]
        self.assertEqual(len(fire), 1)
        self.assertEqual((fire[0].t0 + fire[0].t1) / 2, 74.5)  # centred on the find-spot glint
        self.assertTrue(71.5 < fire[0].t0 and fire[0].t1 < 79.0)  # in bay 5, the one without a north lancet
        end = [o for o in ops if o.piece == "kit_window_end_9x12"]
        self.assertEqual((end[0].run, end[0].t0, end[0].t1, end[0].y0, end[0].y1), ("gh_dais:e0", 15.5, 24.5, 6.0, 18.0))
        arch = [o for o in self.gh.openings if o.kind == "arch"]
        self.assertEqual([(o.door, o.piece, o.reveal, o.leaves) for o in arch], [("gh_doors", "kit_stone_arch_6x9", 1.5,
                                                                                  False)])

    def test_great_hall_heroes(self):
        hero = {h.id: h for h in self.gh.hero}
        trusses = sorted(h.pos[0] for h in hero.values() if h.piece == "kit_truss_archbraced_22")
        self.assertEqual(trusses, [34 + G.GH_BAY * k for k in range(9)])
        self.assertEqual({h.pos[1] for h in hero.values() if h.piece == "kit_truss_archbraced_22"}, {G.GH_CORBEL})
        beasts = [h for h in hero.values() if h.piece.startswith("kit_beast_brazier_")]
        self.assertEqual(len(beasts), 14)
        self.assertEqual(sorted({h.pos[0] for h in beasts}), [41.5, 49.0, 56.5, 64.0, 71.5, 79.0, 86.5])
        designs = [h.piece[-1] for h in beasts]
        self.assertEqual(sorted(set(designs)), ["a", "b", "c", "d"])
        self.assertTrue(all(designs.count(x) >= 3 for x in "abcd"))
        for h in beasts:
            self.assertGreaterEqual(h.pos[1] - dict(h.args)["drop"], G.OVERHEAD_CLEARANCE + 2.0)
        self.assertEqual(sum(1 for h in hero.values() if h.piece.startswith("kit_banner_")
                             and not h.piece.startswith(G.CUP_BANNER)), 8)
        self.assertEqual(sorted(h.piece for h in hero.values() if h.piece.startswith(G.CUP_BANNER)),
                         [f"{G.CUP_BANNER}_{x}" for x in "abcd"])
        for need in ("staff_table", "staff_chairs", "staff_chair_high", "owl_lectern", "hourglasses"):
            self.assertIn(need, hero)
        self.assertEqual(sum(1 for h in hero.values() if h.piece.startswith("kit_table_house_")), 4)
        self.assertEqual(sum(1 for h in hero.values() if h.piece.startswith("kit_bench_")), 8)
        # The hourglasses stand by the landmark that names them.
        hp = next(lm for lm in load(LAYOUT_PATH)["landmarks"] if lm["id"] == "house_points")
        self.assertAlmostEqual(hero["hourglasses"].pos[0], hp["pos"][0])
        # Every hero clear of the dueling area (keep_clear) below 2.4 m.
        area = next(a for a in load(LAYOUT_PATH)["areas"] if a["id"] == "dueling_club")
        for h in hero.values():
            inside = area["min"][0] < h.pos[0] < area["max"][0] and area["min"][2] < h.pos[2] < area["max"][2]
            self.assertFalse(inside and h.pos[1] < 2.4, h.id)

    def test_great_hall_keeps_the_dueling_club(self):
        self.assertEqual([a for a, _, _ in self.gh.keep_clear], ["dueling_club"])
        for kept in ("dc_stage", "dc_area", "dc_bench_nw", "dc_banner_nw"):
            self.assertNotIn(kept, self.gh.replaces)
        self.assertIn("dc_candles", self.gh.replaces)  # the hall's candle groups fly over the stage too
        d = self.d()
        d["cells"]["great_hall"]["hero"].append({"id": "x", "piece": "kit_bench_13", "pos": [67, 0, 16], "yaw": 0})
        self.assertError(d, "stands in area dueling_club")
        d = self.d()
        d["cells"]["great_hall"]["keep_clear"] = ["nowhere"]
        self.assertError(d, "is not a layout area")

    def test_great_hall_sky(self):
        sky = self.gh.sky
        self.assertEqual(len(sky), 18)  # 9 line bays x 2 pitches
        us = sorted({u for p in sky for u, _ in p.uv})
        self.assertEqual((us[0], us[-1]), (0.0, 1.0))
        for p in sky:
            self.assertLess(p.normal[1], 0)  # faces the hall below
            n = geom_newell(p.corners)
            self.assertGreater(sum(a * b for a, b in zip(n, p.normal)), 0)  # wound to match
            self.assertEqual(sorted({v for _, v in p.uv}), [0.0, 1.0])
            ys = sorted({c[1] for c in p.corners})
            self.assertEqual(ys, [17.5, 28.5])
        d = self.d()
        d["cells"]["great_hall"]["sky"]["ridge"] = 28.9
        self.assertError(d, "sky.ridge")
        d = self.d()
        d["cells"]["great_hall"]["sky"]["plate"] = 15.0
        self.assertError(d, "sky.plate must be the springing line")

    def test_great_hall_candles(self):
        cs = self.gh.candles
        self.assertEqual([c.id for c in cs], [f"candles_{k}" for k in range(4)])
        self.assertEqual(sum(c.count for c in cs), 600)
        for c in cs:
            self.assertGreaterEqual(c.y0 - c.amp - G.CANDLE_HEIGHT, 0.9 + G.OVERHEAD_CLEARANCE)  # over the stage
            self.assertEqual(c.lod1, G.LOD_CANDLES)
            spec = self.gh.groups[c.id]
            self.assertEqual(dict(spec.anim)["kind"], "bob")
        d = self.d()
        d["cells"]["great_hall"]["candles"][0]["y"] = [2.5, 4]
        self.assertError(d, "over the floor below")
        d = self.d()
        d["cells"]["great_hall"]["candles"].append(dict(d["cells"]["great_hall"]["candles"][0], id="candles_4"))
        self.assertError(d, "at most 4 candle groups")
        d = self.d()
        d["cells"]["great_hall"]["candles"][1]["id"] = "candles_7"
        self.assertError(d, "id must be candles_1")
        d = self.d()
        d["cells"]["great_hall"]["candles"][1]["rect"] = [40, 11, 60, 29]
        self.assertError(d, "overlaps candles_0")
        d = self.d()
        d["cells"]["great_hall"]["candles"][0]["bob"]["amp"] = 0.5
        self.assertError(d, "bob amp")

    def test_great_hall_draw_budget(self):
        # wall, floor, trim, wood (trusses, wainscot, tables), glass, props, sky, cloth + 4 candle groups.
        static = ["castle_wall_b", "castle_floor_long", "castle_trim", "castle_wood", "castle_glass", "castle_props",
                  "castle_sky", "castle_cloth"]
        self.assertLessEqual(len(static) + len(self.gh.candles), G.BUDGETS["great_hall"].draws)

    def test_line_errors(self):
        d = self.d()
        d["cells"]["great_hall"]["lines"]["at"] = [41.5, 42]
        self.assertError(d, "lines.at")
        d = self.d()
        d["cells"]["great_hall"]["lines"]["top"] = 30
        self.assertError(d, "lines.top")
        d = self.d()
        d["cells"]["great_hall"]["runs"]["gh_floor:n0"] = {"bays": 2}
        self.assertError(d, "follow the cell's lines")
        d = self.d()
        op = d["cells"]["great_hall"]["openings"][0]
        op["line_bays"] = [3]  # bay 3 (56.5..64) is not in gh_floor:n0 (34..56.5)
        self.assertError(d, "is not inside gh_floor:n0")
        d = self.d()
        d["cells"]["gf_corridor_west"]["openings"][0] = {"kind": "niche", "piece": "kit_niche_2x3.5",
                                                         "run": "gf_corridor_west:n0", "line_bays": [0], "sill": 0.6}
        self.assertError(d, "line_bays needs the cell's lines")
        d = self.d()
        d["cells"]["great_hall"]["openings"][0]["sill"] = 3.0
        self.assertError(d, "clear the 3.2 m wainscot")
        d = self.d()
        d["cells"]["great_hall"]["openings"][2]["sill"] = 0.6
        self.assertError(d, "a fireplace stands on the floor")

    # -- the Grand Staircase ---------------------------------------------------------------

    def test_grand_staircase_cells(self):
        lo, hi = self.cells["grand_staircase"], self.cells["grand_staircase_upper"]
        for cp, sheet in ((lo, "castle_portraits_a"), (hi, "castle_portraits_b")):
            self.assertEqual((cp.budget, cp.shaft, cp.railings), ("grand_staircase", "none", "gallery_edge"))
            self.assertEqual(cp.materials["castle_portraits"], sheet)
            self.assertEqual(cp.shafts, [])
        self.assertEqual(G.BUDGETS["grand_staircase"].cells, 2)
        doors = {o.door for cp in (lo, hi) for o in cp.openings if o.kind == "arch"}
        # Castle M5a N1: the ground floor's east wall also opens onto the Undercroft Walk.
        # The east doors into the east wing (L3) and the seventh-floor east corridor (L7).
        east = {"gs_door_l3_east", "gs_door_l3_east_n", "gs_door_l7_east", "gs_door_l7_east_n"}
        self.assertEqual(doors, {f"gs_door_l{n}" for n in range(8)} | {"gs_door_l0_north"} | east)
        self.assertEqual({o.piece for cp in (lo, hi) for o in cp.openings if o.kind == "arch" and o.door not in east},
                         {"kit_stone_arch_5x6"})

    def test_portrait_clusters(self):
        lo, hi = self.cells["grand_staircase"], self.cells["grand_staircase_upper"]
        self.assertEqual(sorted(lo.groups), ["l0", "l1", "l2", "l3"])
        self.assertEqual(sorted(hi.groups), ["l4", "l5", "l6", "l7"])
        for cp in (lo, hi):
            for band, spec in cp.groups.items():
                self.assertEqual(spec.lod_distance, G.LOD_STOREY)
            for pc in cp.portraits:
                level = int(pc.band[1:])
                self.assertAlmostEqual(pc.y0, G.level_y(level) + 0.9)
                self.assertAlmostEqual(pc.y1, G.level_y(level) + 7.5)
        walls = {}
        for cp in (lo, hi):
            for pc in cp.portraits:
                walls.setdefault(pc.band, set()).add((pc.axis, pc.c, pc.sign))
        self.assertEqual(sorted(walls), [f"l{n}" for n in range(8)])
        for band, faces in walls.items():
            self.assertEqual(len(faces), 4, band)  # every wall of every level
        area = sum((pc.t1 - pc.t0) * (pc.y1 - pc.y0) for cp in (lo, hi) for pc in cp.portraits)
        self.assertGreater(area, 6000)  # about 6,200 m^2 of wall
        d = self.d()
        d["cells"]["grand_staircase"]["portraits"][0]["t"] = [-65.4, -20]
        self.assertError(d, "no wall over")
        d = self.d()
        d["cells"]["grand_staircase"]["portraits"][4]["t"] = [-3.8, 5.5]
        self.assertError(d, "reaches gs_floor_centre:e:arch:gs_door_l0")
        d = self.d()
        d["cells"]["grand_staircase"]["portraits"][3]["t"] = [-15.4, -10.5]
        self.assertError(d, "reaches gs_floor_north:e:arch:gs_door_l0_north")
        d = self.d()
        del d["cells"]["grand_staircase"]["materials"]["castle_portraits"]
        self.assertError(d, "portraits need materials.castle_portraits")

    def test_lanterns_on_spots(self):
        for cid in ("grand_staircase", "grand_staircase_upper"):
            lan = self.cells[cid].lanterns
            self.assertEqual(len(lan), 24, cid)  # six a level
            self.assertEqual({lt.shaft for lt in lan}, {""})
        d = self.d()
        d["cells"]["grand_staircase"]["lanterns"]["spots"][0]["t"] = [-31.5]
        errs = self.errors(d)
        self.assertEqual(errs, [])  # a lantern near the corner is fine
        d["cells"]["grand_staircase"]["lanterns"]["spots"][0]["t"] = [-20]
        self.assertError(d, "no wall for a lantern")
        d = self.d()
        d["cells"]["grand_staircase"]["lanterns"]["spots"].append({"run": "gs_floor_centre:e0", "t": [5.5]})
        self.assertError(d, "stands on gs_floor_centre:e:arch:gs_door_l0")

    def test_every_landing_is_carried(self):
        hero = [h for cid in ("grand_staircase", "grand_staircase_upper") for h in self.cells[cid].hero]
        landings = [s for s in self.layout.surfaces if s.id.startswith("gs_landing_")]
        self.assertEqual(len(landings), 10)
        for s in landings:
            supported = [h for h in hero if h.piece.startswith("kit_landing_corbel_")
                         and s.x0 - 1e-6 <= h.pos[0] <= s.x1 + 1e-6 and s.z0 - 1e-6 <= h.pos[2] <= s.z1 + 1e-6
                         and abs(h.pos[1] - s.y) < 1e-6]
            self.assertEqual(len(supported), 1, s.id)
        piers = [s for s in self.layout.surfaces if s.id.startswith("gs_pier_")]
        pier = next(h for h in hero if h.id == "pier")
        self.assertEqual(dict(pier.args)["top"], max(s.y for s in piers))
        for s in piers:
            self.assertAlmostEqual((s.x0 + s.x1) / 2, pier.pos[0])
            self.assertAlmostEqual((s.z0 + s.z1) / 2, pier.pos[2])

    def test_guardian_portrait(self):
        l7 = self.cells["l7_corridor_north"]
        g = next(h for h in l7.hero if h.id == "entrance_cr_a_portrait")
        door = self.layout.by_id("cr_a_portrait")
        self.assertEqual(g.piece, "kit_portrait_guardian_a")
        self.assertAlmostEqual(g.pos[0], (door.x0 + door.x1) / 2)
        self.assertAlmostEqual(g.pos[2], door.z1)
        self.assertEqual(l7.materials["castle_portraits"], G.GUARDIAN_CANVAS[0])
        self.assertTrue({"cr_a_portrait_canvas", "cr_a_portrait_dress"} <= set(l7.replaces))

    def test_replaced_repeat_rows(self):
        hi = self.cells["grand_staircase_upper"]
        self.assertIn("gs_l4_frame_n_7", hi.replaces)
        self.assertNotIn("gs_l4_frame_n", hi.replaces)


class D1ContractTests(unittest.TestCase):
    """Castle D1, the depth pass (docs/art/castle-kit.md): the bake's
    numbers, the greyscale COLOR_0 rule, the loops' budget, castle_kit.json `shade`."""

    @classmethod
    def setUpClass(cls):
        cls.layout = L.load_layout(LAYOUT_PATH)
        cls.data = load(KIT_PATH)
        cls.plan, cls.rep = S.resolve(cls.data, cls.layout, PROPS_PATH)
        assert cls.rep.ok, cls.rep.errors

    def errors(self, data):
        _, rep = S.resolve(data, self.layout, PROPS_PATH)
        return rep.errors

    def test_bake_numbers_cover_every_budget_kind(self):
        self.assertEqual(set(G.AO_RADIUS), set(G.BUDGETS))
        self.assertEqual(set(G.SLICE_ADD_MAX), set(G.BUDGETS))
        self.assertEqual((G.ao_radius("corridor"), G.ao_radius("grand_staircase")), (2.0, 4.0))
        self.assertEqual((G.ao_rays("great_hall"), G.ao_rays("corridor")), (128, 64))
        for kind in G.BUDGETS:
            lo, hi = G.AO_RADIUS_RANGE
            self.assertTrue(lo <= G.ao_radius(kind) <= hi, kind)

    def test_rays_and_weights(self):
        rays = G.hammersley(64)
        self.assertEqual(rays, G.hammersley(64))  # fixed by n: the bake is deterministic
        self.assertEqual(len(set(rays)), 64)
        for x, y, z in rays:
            self.assertAlmostEqual(x * x + y * y + z * z, 1.0, places=9)
            self.assertGreater(z, 0.0)
        mean_z = sum(z for _, _, z in rays) / len(rays)
        self.assertAlmostEqual(mean_z, 2.0 / 3.0, delta=0.01)  # cosine-weighted
        self.assertEqual(G.ao_weight(0.0, 2.0), 1.0)
        self.assertEqual(G.ao_weight(2.0, 2.0), 0.0)
        self.assertAlmostEqual(G.ao_weight(1.0, 2.0), 0.75)

    def test_shade_value_and_byte(self):
        self.assertEqual(G.shade_value(1.0, 1.0), 1.0)
        self.assertEqual(G.shade_value(0.0, 1.0), G.SHADE_MIN)
        self.assertAlmostEqual(G.shade_value(0.5, 0.9), 0.5 ** G.AO_EXPONENT * 0.9)
        self.assertEqual(G.shade_byte(1.0), 255)
        self.assertEqual(G.shade_byte(G.SHADE_MIN), 89)
        self.assertGreater(G.SHADE_WALL_BOTTOM, 0.70)  # gentler than the old wall ramp (the AO darkens the base)

    def test_colour0_grey_rule(self):
        self.assertTrue(G.colour0_grey(bytes([89, 89, 89, 255, 255, 255, 255, 255])))
        self.assertFalse(G.colour0_grey(bytes([89, 90, 89, 255])))
        self.assertFalse(G.colour0_grey(bytes([89, 89, 89, 254])))
        self.assertFalse(G.colour0_grey(bytes([1, 1, 1])))
        self.assertEqual(G.COLOR0_HUE_EXCEPTIONS, ("floor_grounds",))

    def test_slice_budget(self):
        self.assertEqual(G.SLICE_GRID, G.STONE_TILE_M)
        self.assertEqual(G.slice_allowance("corridor", 4_753), 2_000)  # small cells get the floor
        self.assertEqual(G.slice_allowance("great_hall", 69_075), 13_815)  # 20 %
        self.assertEqual(G.slice_allowance("grand_staircase", 130_283), 26_056)
        self.assertEqual(G.slice_allowance("entrance_hall", 400_000), 15_000)  # the cap
        self.assertEqual(G.SLICE_WALL_FROM_FLOOR[0], G.PLINTH.height)
        self.assertEqual(G.SLICE_WALL_FROM_TOP[0], G.CORNICE.height)
        self.assertGreaterEqual(min(G.SLICE_NEAR), 3 * G.SLICE_MERGE)

    def test_triangles_in_view_match_the_client(self):
        self.assertEqual(G.TRIANGLE_BUDGET, {"high": 2_000_000, "medium": 1_200_000, "low": 500_000})
        self.assertEqual(G.CASTLE_IN_VIEW_TARGET, {"high": 400_000, "medium": 300_000, "low": 300_000})
        with open(os.path.join(REPO, "client", "src", "game", "characters", "lod.ts"), encoding="utf-8") as f:
            ts = f.read().replace("_", "")
        for q in ("high", "medium", "low"):
            self.assertIn(f"{q}: {G.TRIANGLE_BUDGET[q]}", ts)
            self.assertIn(f"{q}: {G.CASTLE_IN_VIEW_TARGET[q]}", ts)

    def test_every_kitted_cell_resolves_its_shade(self):
        for cid, cp in {**self.plan.cells, **self.plan.planned}.items():
            sh = self.data["cells"][cid].get("shade", {})  # castle M5a: the M5a cells set theirs
            self.assertEqual(cp.shade, S.Shade(sh.get("radius", G.ao_radius(cp.budget)), sh.get("rays", G.ao_rays(cp.budget))),
                             cid)

    def test_shade_overrides(self):
        d = copy.deepcopy(self.data)
        d["cells"]["great_hall"]["shade"] = {"radius": 3.5, "rays": 96}
        plan, rep = S.resolve(d, self.layout, PROPS_PATH)
        self.assertTrue(rep.ok, rep.errors)
        self.assertEqual(plan.cells["great_hall"].shade, S.Shade(3.5, 96))
        for bad, frag in (({"radius": 9}, "shade.radius"), ({"rays": 8}, "shade.rays"), ({"rays": 64.5}, "shade.rays"),
                          ({"blur": 1}, "shade: unknown field"), ([], "shade must be an object")):
            d = copy.deepcopy(self.data)
            d["cells"]["great_hall"]["shade"] = bad
            errs = self.errors(d)
            self.assertTrue(any(frag in e for e in errs), (bad, errs))



class M5aContractTests(unittest.TestCase):
    """Castle M5a, corridors and towers (docs/art/castle-kit.md): the
    budget kind `tower`, the per-cell guidelines, the new pieces and their
    constants, the eight planned cells in castle_kit.json, castle_tapestry."""

    @classmethod
    def setUpClass(cls):
        cls.layout = L.load_layout(LAYOUT_PATH)
        cls.data = load(KIT_PATH)
        cls.plan, cls.rep = S.resolve(cls.data, cls.layout, PROPS_PATH)
        assert cls.rep.ok, cls.rep.errors
        cls.cps = {**cls.plan.cells, **cls.plan.planned}
        cls.textures = load(TEXTURES_PATH)

    def test_budgets(self):
        # + the knocker door's node; Knocker door opens as a pair: + 3 (a node per leaf).
        self.assertEqual(G.BUDGETS["tower"], G.Budget(12, (25_000, 45_000)))
        self.assertEqual(G.BUDGETS["corridor"], G.Budget(8, (10_000, 35_000)))
        self.assertEqual((G.ao_radius("tower"), G.ao_rays("tower"), G.SLICE_ADD_MAX["tower"]), (2.0, 64, 8_000))
        self.assertEqual(set(G.M5A_TRIS), set(G.M5A_CELLS))
        for cid, (lo, hi) in G.M5A_TRIS.items():
            cap = G.BUDGETS[self.cps[cid].budget].tris
            self.assertTrue(cap[0] <= lo < hi <= cap[1], cid)  # guidelines inside the kind's cap
        self.assertEqual(G.M5A_TRIS["l4_corridor_west"], (15_000, 30_000))
        self.assertEqual(G.M5A_TRIS["gf_undercroft"], G.M5A_TRIS["ff_gallery_east"])
        self.assertEqual(G.M5A_TRIS["ff_gallery_east"], (20_000, 35_000))
        self.assertEqual(G.M5A_TRIS["west_tower_stair"], (25_000, 45_000))
        # Minimum spec: the castle's numbers in view are guidelines, unchanged by M5a.
        self.assertEqual(G.CASTLE_IN_VIEW_TARGET, {"high": 400_000, "medium": 300_000, "low": 300_000})

    def test_cells_planned_with_owner_budget_camera_and_shade(self):
        self.assertEqual(sorted(G.M5A_CELLS), sorted(c for c in G.M5A_CELLS if c in self.cps))
        self.assertEqual(set(G.M5A_CELLS.values()), {"corridors", "tower"})
        self.assertEqual([c for c, o in G.M5A_CELLS.items() if o == "tower"], ["west_tower_stair"])
        for cid, owner in G.M5A_CELLS.items():
            cp = self.cps[cid]
            self.assertEqual(cp.budget, "tower" if owner == "tower" else "corridor", cid)
            self.assertIn("shade", self.data["cells"][cid], cid)
            self.assertEqual(self.plan.camera_max(cid), G.M5A_CAMERA_MAX.get(cid, G.CAMERA_MAX_DEFAULT), cid)
            self.assertEqual(cid in self.plan.planned, cid in PLANNED, cid)
        self.assertEqual(G.M5A_CAMERA_MAX, {"gf_undercroft": 20.0, "ff_gallery_east": 20.0, "west_tower_stair": 16.0})
        # The N3 vestibules are surfaces of the l4-l6 corridors; their tower doors are the tower's arches.
        for lv in (4, 5, 6):
            ids = {o.id for o in self.cps[f"l{lv}_corridor_west"].openings}
            self.assertIn(f"l{lv}_vestibule:w:arch:wt_door_l{lv}", ids)
            self.assertIn(f"wt_door_l{lv}", {o.door for o in self.cps["west_tower_stair"].openings if o.kind == "arch"})

    def test_piece_names_and_owners(self):
        self.assertEqual(set(G.M5A_PIECE_OWNER), set(G.M5A_PIECES))
        for name in G.M5A_PIECES:
            self.assertTrue(G.PIECE_RE.match(name), name)
        for name in (G.CLASS_DOOR, G.SLIT_WINDOW, G.ROSE_WINDOW):
            self.assertIn(name, G.OPENING_PIECES)
        self.assertEqual(G.OPENING_PIECES[G.CLASS_DOOR].kind, "blind_door")
        self.assertEqual((G.OPENING_PIECES[G.SLIT_WINDOW].width, G.OPENING_PIECES[G.SLIT_WINDOW].height), (0.6, 2.4))
        self.assertEqual(G.OPENING_PIECES[G.ROSE_WINDOW].kind, "window")
        self.assertLessEqual(G.ROSE_TRIS, 3_000)
        self.assertLessEqual(G.STATUE_TRIS, 2_500)
        # A statue fits its niche (kit_niche_2x3.5) with the clearance kept.
        niche = G.OPENING_PIECES["kit_niche_2x3.5"]
        self.assertLessEqual(G.STATUE_HEIGHT[1], niche.height - G.STATUE_NICHE_CLEAR)
        # Tapestry pieces: w / h equals each tile's aspect; the tiles lie inside the sheet.
        sheet = self.textures["sheets"]["tapestries"]
        tiles = sheet["tiles"]
        self.assertEqual(sorted(t for ts in G.TAPESTRY_PIECES.values() for t in ts), sorted(tiles))
        for piece, ts in G.TAPESTRY_PIECES.items():
            _, size = piece.rsplit("_", 1)
            w, h = (float(v) for v in size.split("x"))
            for t in ts:
                x, y, tw, th = tiles[t]
                self.assertAlmostEqual(tw / th, w / h, places=6, msg=t)
                self.assertTrue(0 <= x and x + tw <= sheet["px"][0] and 0 <= y and y + th <= sheet["px"][1], t)
        self.assertLessEqual(G.TAPESTRY_PROUD, G.INTRUSION_MAX)
        self.assertEqual(G.PORTRAIT_FILL_M5A, (0.65, 0.85))
        self.assertLessEqual(G.SPIRAL_CORE_TRIS + 6 * G.SLIT_TRIS + G.KNOCKER_DOOR_TRIS, G.BUDGETS["tower"].tris[1])

    def test_heroes_and_replacements(self):
        heroes = {(cid, h.id): h for cid in G.M5A_CELLS for h in self.cps[cid].hero}
        used = {h.piece for h in heroes.values()}
        for name in (G.SPIRAL_CORE, G.KNOCKER_DOOR, G.WELL, *G.STATUE_PIECES, *G.TAPESTRY_PIECES):
            self.assertIn(name, used, name)
        core = heroes[("west_tower_stair", "spiral_core")]
        x0, z0, x1, z1 = G.SPIRAL_CORE_RECT
        self.assertEqual((core.pos[0], core.pos[2]), ((x0 + x1) / 2, (z0 + z1) / 2))
        self.assertEqual(self.cps["west_tower_stair"].replaces,
                         ["cr_c_door_leaf", "cr_c_knocker", "wt_core_cap", "wt_torch_0", "wt_torch_1", "wt_torch_2"])
        self.assertIn("charms_door", self.cps["tf_corridor_west"].replaces)
        door = next(o for o in self.cps["tf_corridor_west"].openings if o.piece == G.CLASS_DOOR)
        self.assertEqual((door.t0 + door.t1) / 2, -80.0)  # on the interactable's spot
        well = heroes[("gf_undercroft", "well")]
        self.assertEqual(well.pos, (14.0, 0.0, -8.0))  # the Undercroft Well landmark
        rose = next(o for o in self.cps["ff_gallery_east"].openings if o.piece == G.ROSE_WINDOW)
        self.assertEqual((rose.t0 + rose.t1) / 2, -9.0)  # centred on the gallery (the landmark's z)
        slits = [o for o in self.cps["west_tower_stair"].openings if o.piece == G.SLIT_WINDOW]
        self.assertEqual(len(slits), 6)
        self.assertEqual(sorted(round(o.y0, 2) for o in slits), [30.45, 32.7, 39.45, 41.7, 46.2, 52.95])  # they climb

    def test_tapestry_material(self):
        m = self.textures["materials"]["castle_tapestry"]
        self.assertEqual((m["texture"], m["px"], m["map"], m["ktx2"]["mode"], m["milestone"]),
                         ("tapestries", [1024, 1024], "sheet", "uastc", 5))
        self.assertNotIn("family", m)
        self.assertEqual(self.textures["materials"]["castle_wall_c"]["texture"], "stone_ashlar_c")
        self.assertGreaterEqual(T.MILESTONE, 5)  # castle M5a moved it to 5 with the painter (castle M5b: 6)


class M5bContractTests(unittest.TestCase):
    """Castle M5b, rooms (docs/art/castle-kit.md): the budget kinds
    `classroom` and `room`, the per-cell guidelines, owners, materials, bake and
    camera, the twelve planned cells, the piece names and their constants, the
    seat and floor-seat numbers, the dungeon variants, the still-life mix and
    the ring chandelier's candle group."""

    @classmethod
    def setUpClass(cls):
        cls.layout = L.load_layout(LAYOUT_PATH)
        cls.data = load(KIT_PATH)
        cls.plan, cls.rep = S.resolve(cls.data, cls.layout, PROPS_PATH)
        assert cls.rep.ok, cls.rep.errors
        cls.cps = {**cls.plan.cells, **cls.plan.planned}
        cls.textures = load(TEXTURES_PATH)

    def test_budgets(self):
        self.assertEqual(G.BUDGETS["classroom"], G.Budget(10, (40_000, 70_000)))
        self.assertEqual(G.BUDGETS["room"], G.Budget(10, (30_000, 60_000)))
        self.assertEqual(G.BUDGETS["common_room"], G.Budget(12, (40_000, 70_000)))  # since M1
        self.assertEqual(G.BUDGETS["corridor"], G.Budget(8, (10_000, 35_000)))  # unchanged since M5a
        for kind in ("classroom", "room"):
            self.assertEqual((G.ao_radius(kind), G.ao_rays(kind)), (2.5, 64), kind)
        self.assertEqual((G.SLICE_ADD_MAX["classroom"], G.SLICE_ADD_MAX["room"]), (10_000, 8_000))
        self.assertEqual(set(G.M5B_TRIS), set(G.M5B_CELLS))
        self.assertEqual(set(G.M5B_BUDGET), set(G.M5B_CELLS))
        for cid, (lo, hi) in G.M5B_TRIS.items():
            cap = G.BUDGETS[G.M5B_BUDGET[cid]].tris
            self.assertTrue(cap[0] <= lo < hi <= cap[1], cid)  # guidelines inside the kind's cap
        self.assertEqual(G.M5B_TRIS["dungeon_classroom"][1], 70_000)  # the Potions room stays at most 70 k
        # Minimum spec: nothing for Low alone, the castle's numbers in view unchanged.
        self.assertEqual(G.CASTLE_IN_VIEW_TARGET, {"high": 400_000, "medium": 300_000, "low": 300_000})
        self.assertEqual(G.TRIANGLE_BUDGET, {"high": 2_000_000, "medium": 1_200_000, "low": 500_000})

    def test_cells_planned_with_owner_budget_materials_camera_and_shade(self):
        self.assertFalse(set(G.M5B_CELLS) & set(G.M5A_CELLS))
        self.assertEqual(set(G.M5B_CELLS.values()), {"dungeons", "upper_rooms"})
        self.assertEqual(G.M5B_BUILDERS, ("furniture", "dungeons", "upper_rooms"))
        # The layout's extra loops go with the builder of their level.
        self.assertEqual((G.M5B_CELLS["scullery_passage"], G.M5B_CELLS["trophy_stair"]), ("dungeons", "upper_rooms"))
        layout_cells = {s.cell for s in self.layout.surfaces}
        for cid, owner in G.M5B_CELLS.items():
            self.assertIn(cid, layout_cells, cid)
            cp = self.cps[cid]
            self.assertEqual(cp.budget, G.M5B_BUDGET[cid], cid)
            wall, floor = G.M5B_MATERIALS[cid]
            self.assertEqual((cp.materials["castle_wall"], cp.materials["castle_floor"]), (wall, floor), cid)
            self.assertEqual((cp.shade.radius, cp.shade.rays), G.M5B_SHADE[cid], cid)
            self.assertIn("shade", self.data["cells"][cid], cid)
            self.assertEqual(self.plan.camera_max(cid), G.M5B_CAMERA_MAX.get(cid, G.CAMERA_MAX_DEFAULT), cid)
            self.assertEqual(cid in self.plan.planned, cid in PLANNED, cid)
            # Every door edge has its arch (the schema requires them).
            self.assertTrue(any(o.kind == "arch" for o in cp.openings), cid)
        # Common room D, the dungeons and Potions in the dungeon stone; the east basement warm.
        for cid in ("dungeon_west", "dungeon_classroom", "common_room_house_d", "scullery_passage"):
            self.assertEqual(G.M5B_MATERIALS[cid], ("castle_wall_d", "castle_floor_dungeon"), cid)
        for cid in ("basement_east", "kitchens", "common_room_house_b"):
            self.assertEqual(G.M5B_MATERIALS[cid][0], "castle_wall_b", cid)

    def test_cauldrons_stay(self):
        kit_ids = {k["id"]: k for k in load(PROPS_PATH)["kit"]}
        for kid in G.M5B_KEEP_KIT:
            self.assertEqual((kit_ids[kid]["cell"], kit_ids[kid]["model"]), ("dungeon_classroom", "classroom_cauldron"), kid)
            self.assertNotIn(kid, self.cps["dungeon_classroom"].replaces, kid)

    def test_piece_names_and_owners(self):
        self.assertEqual(set(G.M5B_PIECE_OWNER), {*G.FURNITURE_PIECES, *G.DUNGEON_PIECES, *G.UPPER_PIECES})
        self.assertEqual(len(G.M5B_PIECE_OWNER), len(G.FURNITURE_PIECES) + len(G.DUNGEON_PIECES) + len(G.UPPER_PIECES))
        for name in G.M5B_PIECE_OWNER:
            self.assertTrue(G.PIECE_RE.match(name), name)
            self.assertNotIn(name, G.M5A_PIECES)
        # Sized families take their sizes into the name (grid.piece_name).
        for name in ("kit_sofa_2.4", "kit_rug_4x3", "kit_vault_barrel_9x28", "kit_bookcase_3x4.5", "kit_floor_inlay_map_8x6",
                     "kit_dome_starry_16"):
            self.assertTrue(G.PIECE_RE.match(name), name)
        # House accents only by the palette names house_a..d (an args index).
        self.assertEqual(G.FURNITURE_COVERS[:4], ("house_a", "house_b", "house_c", "house_d"))
        self.assertEqual(G.RUG_FIELDS[:4], ("house_a", "house_b", "house_c", "house_d"))
        for name in (*G.FURNITURE_COVERS, *G.RUG_FIELDS):
            self.assertTrue(G.PALETTE_NAME_RE.match(name), name)
        palette = self.textures["palette"]
        for h in "abcd":
            self.assertIn(f"house_{h}", palette)
        self.assertLessEqual(G.CHANDELIER_BOB_AMP, G.CANDLE_BOB_AMP)
        self.assertTrue(G.CANDLE_BOB_PERIOD[0] <= G.CHANDELIER_BOB_PERIOD <= G.CANDLE_BOB_PERIOD[1])
        self.assertLessEqual(G.CHANDELIER_COUNT[1], G.CANDLE_COUNT[1])
        self.assertLessEqual(G.RUG_THICK + 0.01, G.FLOOR_SEAT_HEIGHT_MAX)

    def test_seat_numbers(self):
        # The seated clip: heels on a floor SEAT_TOP below the seat at 1.88 m; knees 0.50 m ahead.
        self.assertTrue(G.SEAT_TOP_RANGE[0] <= G.SEAT_TOP <= G.SEAT_TOP_RANGE[1])
        self.assertEqual(G.SEAT_TOP, 0.46)
        self.assertLess(G.SEAT_FRONT_MAX, G.KNEE_REACH)  # the calves hang free of the seat
        self.assertLess(G.KNEE_REACH, G.KNEE_CLEAR_AHEAD)
        self.assertLessEqual(G.KNEE_CLEAR_AHEAD, 0.65 + 1e-9)  # the Great Hall's tables pass at 0.65 m
        self.assertLess(G.KNEE_TOP, G.KNEE_CLEAR_TOP)
        self.assertGreater(G.ARM_GAP_MIN, 0.39)  # the legs' width at 1.88 m
        self.assertTrue(G.SEAT_ROW_DEPTH[0] < G.SEAT_ROW_DEPTH[1])
        self.assertGreaterEqual(G.SEAT_WIDTH_MIN, 0.5)  # the client keeps a sitter 0.25 m inside a seat's ends
        self.assertEqual(G.FLOOR_SEATS, "floor_seats")
        self.assertLessEqual(G.FLOOR_SEAT_MIN, G.RUG_SIZE[0] - 0.2)

    def test_dungeon_textures(self):
        mats = self.textures["materials"]
        for name, tex, fam in (("castle_wall_d", "stone_ashlar_dungeon", "castle_wall"),
                               ("castle_floor_dungeon", "stone_flag_dungeon", "castle_floor")):
            m = mats[name]
            self.assertEqual((m["family"], m["texture"], m["px"], m["map"], m["tile_m"], m["ktx2"]["mode"], m["milestone"]),
                             (fam, tex, [1024, 1024], "world", 4.8, "etc1s", 6), name)
        self.assertGreaterEqual(T.MILESTONE, 6)  # castle M5b moved it to 6 (castle M5c: 7)
        fams = S.material_families(self.textures)
        self.assertIn("castle_wall_d", fams["castle_wall"])
        self.assertIn("castle_floor_dungeon", fams["castle_floor"])

    def test_still_life_mix(self):
        self.assertIs(G.PORTRAIT_MIXES["still_life"], G.PORTRAIT_FRAMES_STILL_LIFE)
        self.assertIs(G.PORTRAIT_MIXES["gallery"], G.PORTRAIT_FRAMES)
        self.assertEqual(set(G.PORTRAIT_MIX_KIND), set(G.PORTRAIT_MIXES))
        self.assertEqual(G.PORTRAIT_MIX_KIND["still_life"], "still_life")
        self.assertIn("still_life", G.PORTRAIT_KINDS)
        for w, h, shape, weight in G.PORTRAIT_FRAMES_STILL_LIFE:
            self.assertIn(shape, G.PORTRAIT_SHAPES)
            self.assertTrue(0.5 <= w <= 2.0 and 0.5 <= h <= 2.0 and weight > 0, (w, h))

    def _resolve_with(self, cid, hero):
        import copy
        data = copy.deepcopy(self.data)
        data["cells"][cid]["hero"] = hero
        plan, rep = S.resolve(data, self.layout, PROPS_PATH)
        return {**plan.cells, **plan.planned}.get(cid), rep

    def test_chandelier_candle_group(self):
        # Common room A: floor 63, ceiling 75.
        ok = {"id": "chandelier", "piece": G.CHANDELIER, "pos": [-49.5, 69, -40.5], "yaw": 0.0,
              "args": {"group": 1, "chain": 6, "count": 12, "radius": 1.0}}
        cp, rep = self._resolve_with("common_room_house_a", [ok])
        self.assertTrue(rep.ok, rep.errors)
        self.assertEqual(cp.hero_groups, {"chandelier": "candles_1"})
        spec = cp.groups["candles_1"]
        self.assertIsNone(spec.lod_distance)
        self.assertEqual(dict(spec.anim), {"amp": G.CHANDELIER_BOB_AMP, "kind": "bob", "period": G.CHANDELIER_BOB_PERIOD,
                                           "phase": 0.0})
        self.assertTrue(G.GROUP_RE.match("candles_1"))
        low = dict(ok, pos=[-49.5, 63 + G.OVERHEAD_CLEARANCE, -40.5])
        for bad, frag in ((low, "lowest point"), (dict(ok, args={"chain": 6}), "args.group"),
                          (dict(ok, args={"group": 4, "chain": 6}), "args.group"),
                          (dict(ok, args={"group": 1, "chain": 7}), "chain"),
                          (dict(ok, args={"group": 1, "chain": 6, "amp": 0.5}), "bob")):
            _, rep = self._resolve_with("common_room_house_a", [bad])
            self.assertTrue(any(frag in e for e in rep.errors), (bad, rep.errors))
        two = [ok, dict(ok, id="chandelier_b", pos=[-45.5, 69, -40.5])]
        _, rep = self._resolve_with("common_room_house_a", two)
        self.assertTrue(any("is taken" in e for e in rep.errors), rep.errors)


class M5cContractTests(unittest.TestCase):
    """Castle M5c, courtyard, facades and exterior (docs/art/castle-kit.md "Exterior"): the budget kind `courtyard`, the planned courtyard, the facing cells and
    their `facade` fields, the facade draw allowance, the exterior material clones
    (`@ext`), the courtyard's plan constants, the piece names and owners, and the
    two milestone-7 textures."""

    @classmethod
    def setUpClass(cls):
        cls.layout = L.load_layout(LAYOUT_PATH)
        cls.data = load(KIT_PATH)
        cls.plan, cls.rep = S.resolve(cls.data, cls.layout, PROPS_PATH)
        assert cls.rep.ok, cls.rep.errors
        cls.cps = {**cls.plan.cells, **cls.plan.planned}
        cls.textures = load(TEXTURES_PATH)

    def test_budget(self):
        self.assertEqual(G.BUDGETS["courtyard"], G.Budget(12, (60_000, 120_000)))
        self.assertEqual((G.ao_radius("courtyard"), G.ao_rays("courtyard")), (3.0, 64))
        self.assertEqual(G.SLICE_ADD_MAX["courtyard"], 12_000)
        self.assertEqual(G.BUDGETS["entrance_hall"], G.Budget(11, (80_000, 120_000)))  # unchanged: the court dressing fits
        self.assertEqual(G.M5C_BUILDERS, ("facades", "courtyard", "client"))
        for cid, (lo, hi) in G.M5C_TRIS.items():
            cap = G.BUDGETS[self.cps[cid].budget].tris
            self.assertTrue(cap[0] <= lo < hi <= cap[1], cid)
        # Minimum spec: nothing for Low alone, the castle's numbers in view unchanged.
        self.assertEqual(G.CASTLE_IN_VIEW_TARGET, {"high": 400_000, "medium": 300_000, "low": 300_000})
        self.assertEqual(G.TRIANGLE_BUDGET, {"high": 2_000_000, "medium": 1_200_000, "low": 500_000})

    def test_courtyard_planned(self):
        self.assertEqual(set(G.M5C_CELLS), {"courtyard"})
        cp = self.cps["courtyard"]
        self.assertEqual("courtyard" in self.plan.planned, "courtyard" in PLANNED)  # built since its pieces exist
        self.assertEqual(cp.budget, "courtyard")
        wall, floor = G.M5C_MATERIALS["courtyard"]
        self.assertEqual((cp.materials["castle_wall"], cp.materials["castle_floor"]), (wall, floor))
        self.assertEqual((cp.shade.radius, cp.shade.rays), G.M5C_SHADE["courtyard"])
        self.assertEqual(self.plan.camera_max("courtyard"), G.M5C_CAMERA_MAX["courtyard"])
        arches = {o.door: o.piece for o in cp.openings if o.kind == "arch"}
        self.assertEqual(arches, {"eh_front_doors": "kit_stone_arch_8x10", "eh_owl_gate": "kit_stone_arch_3x4.5",
                                  "courtyard_gate": "kit_stone_arch_8x10",
                                  "clock_gate_west": "kit_stone_arch_5x6"})  # To the Clock Tower
        self.assertFalse(any(o.leaves for o in cp.openings if o.kind == "arch"))  # the hall's front-door arch has them
        court = self.layout.by_id("courtyard")
        self.assertEqual((court.x0, court.z0, court.x1, court.z1), G.COURT_RECT)
        self.assertEqual(G.COURT_WALL_TOP, 16.0)

    def test_courtyard_plan_constants(self):
        x0, z0, x1, z1 = G.COURT_RECT
        marks = {m["id"]: m["pos"] for m in self.layout.landmarks}
        yx0, yz0, yx1, yz1 = G.EXCHANGE_YARD
        self.assertTrue(x0 <= yx0 < yx1 <= x1 and z0 <= yz0 < yz1 <= z1)
        self.assertTrue(yx0 <= marks["exchange_yard"][0] <= yx1 and yz0 <= marks["exchange_yard"][2] <= yz1)
        self.assertEqual(tuple(marks["courtyard_fountain"][::2]), G.FOUNTAIN_CENTRE)
        doors = {d: self.layout.by_id(d) for d in ("eh_front_doors", "eh_owl_gate", "courtyard_gate")}
        lanes = {"front_doors": "eh_front_doors", "owl_gate": "eh_owl_gate", "courtyard_gate": "courtyard_gate"}
        self.assertEqual(set(G.COURT_KEEP_CLEAR), set(lanes))
        for lane, did in lanes.items():
            a0, b0, a1, b1 = G.COURT_KEEP_CLEAR[lane]
            d = doors[did]
            self.assertTrue(x0 <= a0 < a1 <= x1 and z0 <= b0 < b1 <= z1, lane)
            self.assertTrue(a0 <= d.x0 and d.x1 <= a1, lane)  # the whole opening's width
        # The fountain keeps clear of every lane and the yard.
        fx, fz = G.FOUNTAIN_CENTRE
        r = G.FOUNTAIN_RADIUS[1]
        for a0, b0, a1, b1 in (*G.COURT_KEEP_CLEAR.values(), G.EXCHANGE_YARD):
            self.assertTrue(fx + r <= a0 or fx - r >= a1 or fz + r <= b0 or fz - r >= b1)
        # The window over the front doors (castle_kit.json: the hall's `over` opening) seen from outside.
        eh = self.cps["entrance_hall"]
        win = next(o for o in eh.openings if o.kind == "window" and o.door == "eh_front_doors")
        wx0, wx1, wy0, wy1 = G.FRONT_WINDOW_OUTSIDE
        self.assertEqual((win.t0, win.t1, win.y0), (wx0, wx1, wy0))
        self.assertLess(wy0, G.COURT_WALL_TOP)
        self.assertGreater(win.y1, G.COURT_WALL_TOP)  # its head rises above the courtyard's wall
        # The hall's court dressing keeps the counter, the gate's approach and the front leaf clear.
        cx0, cz0, cx1, cz1 = G.EH_COURT
        gate = doors["eh_owl_gate"]
        ga0, gb0, ga1, gb1 = G.EH_COURT_KEEP_CLEAR["owl_gate"]
        self.assertTrue(ga0 <= gate.x0 and gate.x1 <= ga1 and gb1 == gate.z0)
        ox, _, oz = next(i["pos"] for i in load(LAYOUT_PATH)["interactables"] if i["id"] == "owl_exchange")
        ca0, cb0, ca1, cb1 = G.EH_COURT_KEEP_CLEAR["counter"]
        self.assertTrue(ca0 <= ox - 3.5 or ca0 == 0.0)
        self.assertTrue(ox + 3.5 <= ca1 and cb0 <= oz - 3.5 and oz + 3.5 <= cb1)

    def test_facing_cells_and_their_facade_fields(self):
        layout_cells = {s.cell for s in self.layout.surfaces}
        self.assertEqual(set(G.M5C_FACADE_CELLS), {"entrance_hall", "great_hall", "grand_staircase",
                                                   "common_room_house_a", "courtyard",
                                               "clock_courtyard"})  # Its east side
        for cid, sides in G.M5C_FACADE_CELLS.items():
            self.assertIn(cid, layout_cells, cid)
            self.assertTrue(set(sides) <= set(G.SIDES), cid)
            fa = self.cps[cid].facade
            self.assertIsNotNone(fa, cid)
            self.assertEqual(set(fa.sides), set(sides), cid)
            self.assertTrue(fa.ext, cid)
        self.assertTrue(self.cps["entrance_hall"].facade.roof and self.cps["great_hall"].facade.roof)
        self.assertFalse(self.cps["courtyard"].facade.roof)
        # No other cell has one.
        for cid, cp in self.cps.items():
            if cid not in G.M5C_FACADE_CELLS:
                self.assertIsNone(cp.facade, cid)

    def test_facade_field_is_checked(self):
        import copy

        def errors(cid, fa):
            data = copy.deepcopy(self.data)
            data["cells"][cid]["facade"] = fa
            return S.resolve(data, self.layout, PROPS_PATH)[1].errors

        self.assertEqual(errors("great_hall", {"sides": ["s"]}), [])
        for cid, fa, frag in (("great_hall", {"sides": ["n"]}, "facade.sides"),
                              ("great_hall", {"sides": []}, "facade.sides"),
                              ("great_hall", {"sides": ["s"], "roof": 1}, "facade.roof"),
                              ("great_hall", {"sides": ["s"], "spires": True}, "unknown field"),
                              ("gf_undercroft", {"sides": ["s"]}, "not a facing cell"),
                              ("courtyard", {"sides": ["n"], "roof": True}, "no roof slab")):
            errs = errors(cid, fa)
            self.assertTrue(any(frag in e for e in errs), (cid, fa, errs))

    def test_facade_draw_allowance(self):
        self.assertEqual(G.FACADE_DRAWS, 2)
        self.assertEqual(G.draw_cap("great_hall", "great_hall"), 21)  # 19 + the @ext wall and castle_roof
        self.assertEqual(G.draw_cap("entrance_hall", "entrance_hall"), 13)
        self.assertEqual(G.draw_cap("gf_undercroft", "corridor"), 8)  # not a facing cell
        self.assertEqual(G.draw_cap("courtyard", "courtyard"), 12)  # its own kind counts everything
        self.assertEqual(G.FACADE_LOD, 60.0)

    def test_exterior_clones(self):
        self.assertEqual(G.ext_name("castle_wall_b"), "castle_wall_b@ext")
        self.assertEqual(G.ext_name("castle_wall_b@ext"), "castle_wall_b@ext")
        self.assertEqual(G.ext_name("castle_glass"), "castle_glass")
        self.assertEqual(G.ext_name("castle_roof"), "castle_roof")
        self.assertEqual(G.ext_base("castle_trim@ext"), "castle_trim")
        self.assertEqual(G.ext_base("castle_trim"), "castle_trim")
        self.assertTrue(G.is_outdoor_material("castle_props@ext") and G.is_outdoor_material("castle_floor_court"))
        self.assertFalse(G.is_outdoor_material("castle_wall_b"))
        for tag in ("castle_wall@ext|world", "castle_trim@ext|trim:plinth", "castle_props@ext|kit:metal:@iron",
                    "castle_roof|world"):
            ft = G.parse_face_tag(tag)
            self.assertEqual(ft.material, tag.split("|")[0], tag)
        for bad in ("castle_wall@inside|world", "castle_wall@ext@ext|world"):
            with self.assertRaises(ValueError):
                G.parse_face_tag(bad)
        # A node name keeps one dot between cell and material (the client splits on it).
        self.assertEqual(G.node_name("courtyard", "castle_wall_b@ext"), "courtyard.castle_wall_b@ext")
        mats = self.textures["materials"]
        for m in (*G.EXT_NEVER, *G.OUTDOOR_MATERIALS):
            self.assertIn(m, mats, m)
        for m in G.EXT_PLANNER_FACES:
            self.assertIn(m, L.MATERIALS, m)

    def test_piece_names_and_owners(self):
        self.assertEqual(set(G.M5C_PIECE_OWNER), set(G.M5C_PIECES))
        self.assertEqual(len(G.M5C_PIECES), len(set(G.M5C_PIECES)))
        self.assertEqual(set(G.M5C_PIECE_OWNER.values()), {"facades", "courtyard"})
        for name in G.M5C_PIECES:
            self.assertTrue(G.PIECE_RE.match(name), name)
            self.assertNotIn(name, G.M5A_PIECES)
            self.assertNotIn(name, G.M5B_PIECE_OWNER)
        for name in ("kit_buttress_12", "kit_roof_slate_32x40", "kit_arcade_bay_6", "kit_gatehouse_8x10",
                     "kit_window_tracery_outer_8x8"):
            self.assertTrue(G.PIECE_RE.match(name), name)
        # House accents only by the palette names house_a..d (an args index).
        self.assertEqual(G.STALL_AWNINGS, ("house_a", "house_b", "house_c", "house_d"))
        for h in G.STALL_AWNINGS:
            self.assertIn(h, self.textures["palette"])
        self.assertGreaterEqual(G.PLANTER_TREE_HEIGHT[0], G.OVERHEAD_CLEARANCE + 1.0)
        self.assertGreaterEqual(G.PORTCULLIS_CLEAR, G.DOOR_CLEAR_HEIGHT)
        self.assertGreater(G.GATEHOUSE_TOP[0], G.COURT_WALL_TOP)
        self.assertLessEqual(G.ARCADE_APEX[1], G.COURT_WALL_TOP - 3.0)

    def test_textures(self):
        mats = self.textures["materials"]
        for name, (tex, owner) in G.M5C_TEXTURES.items():
            m = mats[name]
            self.assertEqual((m["texture"], m["px"], m["map"], m["tile_m"], m["ktx2"]["mode"], m["milestone"]),
                             (tex, [1024, 1024], "world", 4.8, "etc1s", G.M5C_MILESTONE), name)
            self.assertIn(owner, G.M5C_BUILDERS)
        self.assertNotIn("family", mats["castle_roof"])
        fams = S.material_families(self.textures)
        self.assertIn("castle_floor_court", fams["castle_floor"])
        self.assertIn(T.MILESTONE, (6, 7))  # each M5c builder moves it to 7 with its painter
        toon = load(TOON_PATH)["materials"]
        self.assertIn("castle_roof", toon)


def geom_newell(pts):
    nx = ny = nz = 0.0
    for i, (x, y, z) in enumerate(pts):
        x2, y2, z2 = pts[(i + 1) % len(pts)]
        nx += (y - y2) * (z + z2)
        ny += (z - z2) * (x + x2)
        nz += (x - x2) * (y + y2)
    return (nx, ny, nz)


if __name__ == "__main__":
    unittest.main()
