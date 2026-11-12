"""Unit tests for castle kit M2's cell visibility (`sees`) and the COLOR_0
byte packing (castle_kit/visibility.py; docs/art/castle-kit.md "Cell visibility (`sees`)", "Shading: the AO bake").
Pure Python; run through tests/run_tests.py."""

import json
import os
import struct
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.dirname(HERE)]

import layout_lib as L  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import visibility as V  # noqa: E402

LAYOUT_PATH = os.path.join(REPO, "data", "world", "layout.json")


def surf(sid, cell, x0, z0, x1, z1, y=0.0, kind="room", ceiling=8.4, **extra):
    d = {"id": sid, "kind": kind, "name": sid, "level": 0, "cell": cell, "min": [x0, z0], "max": [x1, z1], "y": y}
    if ceiling is not None:
        d["ceiling"] = ceiling
    d.update(extra)
    return d


def layout(surfaces):
    return L.parse_layout({"version": 1, "surfaces": surfaces, "spawns": [{"id": "s", "pos": [1, 0, 1], "yaw": 0}]})


def chain(n, door=True):
    """Rooms r0 .. r{n-1} in a row along x, each 10 m, joined by 1 m door surfaces owned by the room before."""
    ss = []
    for i in range(n):
        ss.append(surf(f"r{i}", f"c{i}", i * 11 + 1, 0, i * 11 + 11, 10))
        if door and i + 1 < n:
            ss.append(surf(f"d{i}", f"c{i}", i * 11 + 11, 4, i * 11 + 12, 6, kind="door", ceiling=3))
    return ss


class RealLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = L.load_layout(LAYOUT_PATH)
        cls.sees = V.cell_sees(cls.layout)

    def test_every_cell_has_an_entry(self):
        self.assertEqual(sorted(self.sees), sorted(self.layout.cells()))

    def test_the_hall_sees_its_doors_and_open_joins(self):
        hall = set(self.sees["entrance_hall"])
        for c in ("entrance_hall", "gf_corridor_west", "ff_corridor_north", "great_hall", "courtyard", "dungeon_west", "basement_east"):
            self.assertIn(c, hall)

    def test_both_grand_staircase_cells_see_each_other(self):
        self.assertIn("grand_staircase_upper", self.sees["grand_staircase"])
        self.assertIn("grand_staircase", self.sees["grand_staircase_upper"])
        self.assertEqual(V.stack_groups(self.layout)["grand_staircase"], frozenset({"grand_staircase", "grand_staircase_upper"}))

    def test_three_doors_away_is_out(self):
        # hall -> gf_corridor_west -> grand_staircase -> sf_corridor_west
        self.assertNotIn("sf_corridor_west", self.sees["entrance_hall"])
        # great hall -> hall -> gf_corridor_west -> grand_staircase
        self.assertNotIn("grand_staircase", self.sees["great_hall"])
        self.assertIn("grand_staircase", self.sees["entrance_hall"])

    def test_outdoor_cells_see_everything(self):
        for c in self.layout.cells():
            outdoor = any(s.kind in G.OUTDOOR_KINDS for s in self.layout.surfaces if s.cell == c)
            self.assertEqual(self.sees[c] is None, outdoor, c)
        self.assertIsNone(self.sees["courtyard"])

    def test_sorted_self_included_and_deterministic(self):
        again = V.cell_sees(L.load_layout(LAYOUT_PATH))
        self.assertEqual(again, self.sees)
        for c, seen in self.sees.items():
            if seen is None:
                continue
            self.assertIn(c, seen)
            self.assertEqual(seen, sorted(set(seen)))

    def test_visibility_is_mutual_between_joined_cells(self):
        graph = V.portal_graph(self.layout)
        for a, joined in graph.items():
            for b in joined:
                if self.sees[a] is not None and self.sees[b] is not None:
                    self.assertIn(b, self.sees[a])
                    self.assertIn(a, self.sees[b])


class SyntheticTests(unittest.TestCase):
    def test_doors_up_to_two_deep(self):
        sees = V.cell_sees(layout(chain(5)))
        self.assertEqual(sees["c0"], ["c0", "c1", "c2"])
        self.assertEqual(sees["c2"], ["c0", "c1", "c2", "c3", "c4"])
        self.assertEqual(G.SEES_DOORS, 2)

    def test_no_join_no_sight(self):
        sees = V.cell_sees(layout(chain(3, door=False)))
        self.assertEqual(sees["c0"], ["c0"])

    def test_open_join_counts_as_a_hop(self):
        # A stair whose top edge meets the room above's floor: no door surface.
        ss = [surf("hall", "a", 0, 0, 10, 10), surf("steps", "b", 10, 0, 16, 4, y=-3.0, y_top=0.0, rise="-x", ceiling=10)]
        self.assertEqual(V.cell_sees(layout(ss))["b"], ["a", "b"])

    def test_stacked_levels_cost_no_hop(self):
        # c0 is an unroofed shaft; c1 a gallery over it at 9 m with a door out to c2, beside the shaft.
        ss = [surf("shaft", "c0", 0, 0, 10, 10, ceiling=None),
              surf("gallery", "c1", 0, 0, 3, 10, y=9.0),
              surf("gdoor", "c1", 1, 10, 2, 11, y=9.0, kind="door", ceiling=3),
              surf("next", "c2", 0, 11, 10, 20, y=9.0),
              surf("far", "c3", 11, 11, 20, 20, y=9.0),
              surf("fdoor", "c3", 10, 14, 11, 16, y=9.0, kind="door", ceiling=3),
              surf("farther", "c4", 21, 11, 30, 20, y=9.0),
              surf("f2door", "c4", 20, 14, 21, 16, y=9.0, kind="door", ceiling=3)]
        lay = layout(ss)
        self.assertEqual(V.stack_groups(lay)["c0"], frozenset({"c0", "c1"}))
        sees = V.cell_sees(lay)
        # c1 is in c0's group, so c2 is one hop and c3 two; c4 is three.
        self.assertEqual(sees["c0"], ["c0", "c1", "c2", "c3"])
        self.assertEqual(sees["c3"], ["c0", "c1", "c2", "c3", "c4"])

    def test_a_roofed_room_below_is_not_stacked(self):
        ss = [surf("low", "c0", 0, 0, 10, 10, ceiling=8.4), surf("high", "c1", 0, 0, 10, 10, y=9.0)]
        self.assertEqual(V.stack_groups(layout(ss))["c0"], frozenset({"c0"}))
        ss = [surf("low", "c0", 0, 0, 10, 10, ceiling=12.0), surf("high", "c1", 0, 0, 3, 10, y=9.0)]
        self.assertEqual(V.stack_groups(layout(ss))["c0"], frozenset({"c0", "c1"}))

    def test_an_outdoor_cell_one_hop_away_brings_its_facades(self):
        # hall c0 opens onto a yard; c1 stands beside the yard (2 m away), joined to nothing c0 sees.
        ss = [surf("hall", "c0", 0, 0, 10, 10),
              surf("door", "c0", 4, 10, 6, 11, kind="door", ceiling=4),
              surf("yard", "y", 0, 11, 30, 30, kind="courtyard", ceiling=None),
              surf("tower", "c1", 32, 11, 40, 20),
              surf("far", "c2", 40, 40, 50, 50)]
        sees = V.cell_sees(layout(ss))
        self.assertIsNone(sees["y"])
        self.assertEqual(sees["c0"], ["c0", "c1", "y"])
        self.assertNotIn("c2", sees["c0"])
        self.assertEqual(sees["c1"], ["c1"])  # c1 has no door to the yard

    def test_unplaced_cells_show_wherever_an_outdoor_cell_does(self):
        # The lake (a props.json plane, no layout surface) shows from c0, which sees the yard.
        ss = [surf("hall", "c0", 0, 0, 10, 10),
              surf("door", "c0", 4, 10, 6, 11, kind="door", ceiling=4),
              surf("yard", "y", 0, 11, 30, 30, kind="courtyard", ceiling=None),
              surf("cellar", "c2", 40, 40, 50, 50)]
        sees = V.add_unplaced(V.cell_sees(layout(ss)), ["c0", "c2", "y", "lake"])
        self.assertEqual(sees["c0"], ["c0", "lake", "y"])
        self.assertEqual(sees["c2"], ["c2"])
        self.assertIsNone(sees["y"])
        self.assertNotIn("lake", sees)


def _glb(doc, binary):
    js = json.dumps(doc).encode()
    js += b" " * (-len(js) % 4)
    binary += b"\0" * (-len(binary) % 4)
    return struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js) + 8 + len(binary)) + struct.pack("<II", len(js), 0x4E4F534A) + js + struct.pack("<II", len(binary), 0x004E4942) + binary


def sample_glb():
    """Two primitives: float VEC3 colours (Blender's older path) and normalized ushort VEC4 (Blender 5)."""
    pos = struct.pack("<9f", 0, 0, 0, 1, 0, 0, 0, 1, 0)
    col3 = struct.pack("<9f", 0.64, 0.8, 1.0, 1.2, -0.1, 0.5, 0.0, 0.25, 0.75)
    idx = struct.pack("<3H", 0, 1, 2) + b"\0\0"
    col4 = struct.pack("<12H", 65535, 32768, 0, 65535, 41943, 41943, 41943, 65535, 1, 2, 3, 4)
    views, binary = [], b""
    for chunk, target in ((pos, 34962), (col3, 34962), (idx, 34963), (col4, 34962)):
        views.append({"buffer": 0, "byteOffset": len(binary), "byteLength": 6 if target == 34963 else len(chunk), "target": target})
        binary += chunk
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": views,
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3", "min": [0, 0, 0], "max": [1, 1, 0]},
            {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5123, "count": 3, "type": "SCALAR"},
            {"bufferView": 3, "componentType": 5123, "normalized": True, "count": 3, "type": "VEC4"},
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "COLOR_0": 1}, "indices": 2}, {"attributes": {"POSITION": 0, "COLOR_0": 3}, "indices": 2}]}],
    }
    return _glb(doc, binary), pos, idx[:6]


class PackColourTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "cell.glb")
        data, self.pos, self.idx = sample_glb()
        with open(self.path, "wb") as f:
            f.write(data)
        self.bin_before = len(V.read_glb(self.path)[1])

    def tearDown(self):
        self.dir.cleanup()

    def accessor_bytes(self, doc, binary, i):
        a = doc["accessors"][i]
        v = doc["bufferViews"][a["bufferView"]]
        start = v.get("byteOffset", 0) + a.get("byteOffset", 0)
        return binary[start:start + v["byteLength"]]

    def test_colours_become_normalized_bytes(self):
        saved = V.pack_color0(self.path)
        doc, binary = V.read_glb(self.path)
        for i in (1, 3):
            a = doc["accessors"][i]
            self.assertEqual((a["componentType"], a["type"], a["normalized"]), (5121, "VEC4", True))
            self.assertNotIn("min", a)
            v = doc["bufferViews"][a["bufferView"]]
            self.assertEqual(v["byteLength"], 12)
            self.assertNotIn("byteStride", v)
            self.assertEqual(v.get("byteOffset", 0) % 4, 0)
            self.assertEqual(v["target"], 34962)
        # Linear values, rounded, clamped; alpha 255 where the source had none.
        self.assertEqual(list(self.accessor_bytes(doc, binary, 1)), [163, 204, 255, 255, 255, 0, 128, 255, 0, 64, 191, 255])
        self.assertEqual(list(self.accessor_bytes(doc, binary, 3)), [255, 128, 0, 255, 163, 163, 163, 255, 0, 0, 0, 0])
        self.assertGreater(saved, 0)
        self.assertEqual(saved, self.bin_before - len(binary))
        self.assertEqual(saved, (36 - 12) + (24 - 12))  # float VEC3 and ushort VEC4 to 4 bytes a vertex

    def test_other_accessors_keep_their_bytes(self):
        V.pack_color0(self.path)
        doc, binary = V.read_glb(self.path)
        self.assertEqual(self.accessor_bytes(doc, binary, 0), self.pos)
        self.assertEqual(self.accessor_bytes(doc, binary, 2)[:6], self.idx)
        self.assertEqual(doc["accessors"][0]["min"], [0, 0, 0])
        self.assertEqual(doc["buffers"][0]["byteLength"], len(binary))
        self.assertEqual(len(binary) % 4, 0)

    def test_idempotent_and_deterministic(self):
        V.pack_color0(self.path)
        with open(self.path, "rb") as f:
            once = f.read()
        self.assertEqual(V.pack_color0(self.path), 0)
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), once)
        other = os.path.join(self.dir.name, "again.glb")
        with open(other, "wb") as f:
            f.write(sample_glb()[0])
        V.pack_color0(other)
        with open(other, "rb") as f:
            self.assertEqual(f.read(), once)

    def test_a_file_without_colours_is_untouched(self):
        doc, binary = V.read_glb(self.path)
        for m in doc["meshes"]:
            for p in m["primitives"]:
                p["attributes"].pop("COLOR_0")
        with open(self.path, "wb") as f:
            f.write(_glb(doc, binary))
        with open(self.path, "rb") as f:
            before = f.read()
        self.assertEqual(V.pack_color0(self.path), 0)
        with open(self.path, "rb") as f:
            self.assertEqual(f.read(), before)



# Castle M5a (docs/art/castle-kit.md): the visibility checks for
# the eight M5a cells, and the world draws in view of every cell on the export.
WORLD = os.path.join(REPO, "client", "public", "assets", "world")
MAX_DRAWS = 1000  # the absolute target (architecture.md): every draw of a frame, world and characters


class M5aVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = L.load_layout(LAYOUT_PATH)
        cls.sees = V.cell_sees(cls.layout)

    def test_the_long_gallery_sees_only_through_its_doors(self):
        # Its windows look outside: the corridor its arch opens and that corridor's neighbours, and
        # (castle M5b layout, the Trophy Stair) the stair its north door opens and the Trophy Room.
        # + the statue passage to the Undercroft and the panel passage beyond the corridor.
        self.assertEqual(self.sees["ff_gallery_east"],
                         ["entrance_hall", "ff_corridor_north", "ff_gallery_east", "gf_undercroft", "grand_staircase",
                          "grand_staircase_upper", "passage_panel", "passage_statue", "trophy_room", "trophy_stair"])

    def test_the_undercroft_sees_the_hall_and_the_staircase_stack(self):
        seen = set(self.sees["gf_undercroft"])
        # castle M5b layout: + trophy_room (N2), trophy_stair; + tf_corridor_east (N9),
        # l7_corridor_east (N11) through the Grand Staircase's stack; + ff_gallery_east and
        # passage_statue (its passage), passage_panel.
        self.assertEqual(len(seen), 23)
        for c in ("entrance_hall", "gf_corridor_west", "grand_staircase", "grand_staircase_upper", "great_hall",
                  "ff_corridor_north", "courtyard", "trophy_room"):
            self.assertIn(c, seen)
        for c in ("entrance_hall", "grand_staircase", "grand_staircase_upper"):
            self.assertIn("gf_undercroft", self.sees[c])

    def test_the_west_tower_joins_four_corridors_and_common_room_c(self):
        # + the tapestry and painting passages off its corridors.
        self.assertEqual(self.sees["west_tower_stair"],
                         ["common_room_house_c", "grand_staircase", "grand_staircase_upper", "l4_corridor_west",
                          "l5_corridor_west", "l6_corridor_west", "passage_painting", "passage_tapestry",
                          "tf_corridor_west", "west_tower_stair"])
        for c in ("tf_corridor_west", "l4_corridor_west", "l5_corridor_west", "l6_corridor_west"):
            self.assertIn("west_tower_stair", self.sees[c])

    def test_draws_in_view_sum_the_cells_seen(self):
        sees = {"a": ["a", "b"], "b": ["b"], "y": None}
        self.assertEqual(V.draws_in_view(sees, {"a": 3, "b": 4, "y": 2}), {"a": 7, "b": 4, "y": 9})

    def test_every_cells_world_draws_in_view_stay_under_the_target(self):
        if not os.path.exists(os.path.join(WORLD, "manifest.json")):
            self.skipTest("no export (run client/scripts/prepare.mjs)")
        draws = V.cell_draws(WORLD)
        for c in G.M5A_CELLS:
            self.assertLessEqual(draws[c], G.BUDGETS["tower" if c == "west_tower_stair" else "corridor"].draws, c)
        in_view = V.draws_in_view(V.add_unplaced(self.sees, draws), draws)
        for c, n in in_view.items():
            # The world's share leaves most of the 1,000 to the characters (51 at most a few draws each).
            self.assertLessEqual(n, MAX_DRAWS // 2, c)



# Castle M5b (docs/art/castle-kit.md): the visibility checks for the
# twelve M5b cells. `sees` comes from the layout, so kitting changes no set;
# the cells' own draws on the export stay within their budget kinds, and the rooms' sets
# stay small (their windows look outside or into the lake).
M5B_SEES = {
    # The dungeon cellars (N12) are seen down their stairs from the corridors.
    # The passages are seen through their doors (their walk-through entrances open).
    "kitchens": ["basement_east", "common_room_house_b", "dungeon_cellars", "dungeon_west", "entrance_hall", "kitchens",
                 "scullery_passage"],
    "scullery_passage": ["basement_east", "common_room_house_d", "dungeon_cellars", "dungeon_classroom", "dungeon_west",
                         "entrance_hall", "kitchens", "passage_dungeon", "scullery_passage"],
    "sf_library": ["grand_staircase", "grand_staircase_upper", "passage_painting", "sf_corridor_west", "sf_library",
                   "tf_corridor_west"],
    "trophy_room": ["entrance_hall", "ff_corridor_north", "ff_gallery_east", "gf_undercroft", "grand_staircase",
                    "grand_staircase_upper", "passage_panel", "passage_statue", "trophy_room", "trophy_stair"],
    "trophy_stair": ["ff_corridor_north", "ff_gallery_east", "gf_undercroft", "passage_panel", "passage_statue",
                     "trophy_room", "trophy_stair"],
    "common_room_house_a": ["common_room_house_a", "grand_staircase", "grand_staircase_upper", "l7_corridor_north"],
    "common_room_house_b": ["basement_east", "common_room_house_b", "dungeon_cellars", "entrance_hall", "kitchens"],
    "common_room_house_d": ["common_room_house_d", "dungeon_classroom", "dungeon_west", "entrance_hall", "passage_dungeon",
                            "scullery_passage"],
    "dungeon_classroom": ["common_room_house_d", "dungeon_classroom", "dungeon_west", "entrance_hall", "passage_dungeon",
                          "scullery_passage"],
}


class M5bVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sees = V.cell_sees(L.load_layout(LAYOUT_PATH))

    def test_the_rooms_see_only_through_their_doors(self):
        for cell, want in M5B_SEES.items():
            self.assertEqual(self.sees[cell], want, cell)
        self.assertEqual(len(self.sees["common_room_house_c"]), 6)
        self.assertEqual(len(self.sees["dungeon_west"]), 14)  # + dungeon_cellars, passage_dungeon
        self.assertEqual(len(self.sees["basement_east"]), 13)  # + passage_dungeon

    def test_the_m5b_cells_draw_within_their_budget_kinds(self):
        if not os.path.exists(os.path.join(WORLD, "manifest.json")):
            self.skipTest("no export (run client/scripts/prepare.mjs)")
        draws = V.cell_draws(WORLD)
        for c in G.M5B_CELLS:
            self.assertLessEqual(draws[c], G.BUDGETS[G.M5B_BUDGET[c]].draws, c)
        in_view = V.draws_in_view(V.add_unplaced(self.sees, draws), draws)
        for c in G.M5B_CELLS:
            # A room's world draws in view: far under the 1,000 target (each room sees at most 12 cells).
            # A room that sees the courtyard also counts the outdoor scenery that has no
            # surface (backdrop 2, foliage 4, lake 1, the three all-water grounds cells 3), so 110.
            # dungeon_west also sees its hidden passage (5 draws) and draws its entrance node;
            # And the stone door's (the Serpentyne common room's entrance); basement_east
            # draws the barrel stack's (the Burrowsby entrance's) node.
            self.assertLessEqual(in_view[c], {"dungeon_west": 117, "basement_east": 111}.get(c, 110), c)


# Castle M5c (docs/art/castle-kit.md): the visibility checks. The
# courtyard is outdoor, so it sees every cell (its world draws in view are the whole
# resident castle); the facing cells' facades add draws within grid.draw_cap; the Owl
# Gate changed no set, the hall still sees the courtyard.
class M5cVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sees = V.cell_sees(L.load_layout(LAYOUT_PATH))

    def test_the_courtyard_sees_everything(self):
        self.assertIsNone(self.sees["courtyard"])
        self.assertIn("courtyard", self.sees["entrance_hall"])
        # + clock_courtyard, dungeon_cellars; + passage_dungeon, passage_panel, passage_statue.
        self.assertEqual(len(self.sees["entrance_hall"]), 23)

    def test_the_m5c_cells_draw_within_their_caps_and_far_under_the_target(self):
        if not os.path.exists(os.path.join(WORLD, "manifest.json")):
            self.skipTest("no export (run client/scripts/prepare.mjs)")
        draws = V.cell_draws(WORLD)
        kinds = {"courtyard": "courtyard", "entrance_hall": "entrance_hall", "great_hall": "great_hall",
                 "common_room_house_a": "common_room"}
        for c, kind in kinds.items():
            self.assertLessEqual(draws[c], G.draw_cap(c, kind), c)
        stair = draws["grand_staircase"] + draws["grand_staircase_upper"]
        self.assertLessEqual(stair, G.draw_cap("grand_staircase", "grand_staircase"))
        in_view = V.draws_in_view(V.add_unplaced(self.sees, draws), draws)
        # Outdoor: every resident cell (about 240-250 after M5c, 30.2), far under 1,000.
        # The new wings kitted and the swing nodes, about 330; 400 leaves Wings B room.
        self.assertEqual(in_view["courtyard"], sum(draws.values()))
        self.assertLessEqual(in_view["courtyard"], 400)


if __name__ == "__main__":
    unittest.main()
