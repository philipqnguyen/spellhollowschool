"""Layout tests for castle M5's new spaces (docs/art/castle-kit.md):
one class per space, each added with its layout commit.

Every class checks, on the real data/world/layout.json:
- the space's surfaces as planned (ids, cells, kinds, rectangles, heights);
- the layout check (`layout_lib.validate`) is clean and every new surface is
  reachable from the spawns;
- each new door joins exactly the planned surfaces, at least 3 m wide and
  4.5 m clear, at a size the arch catalogue has (`grid.ARCH_SIZES`), and
  the rooms either side leave that clear height;
- no new surface overlaps any other in 3D (plan overlap together with the
  storey range: the floor slab up to the ceiling);
- the kitted windows near it still open outside (`grid.wall_mass` has
  nothing behind them) and the kit placements resolve without errors;
- its landmarks stand on walkable, reachable ground;
- travel (`scale_pass/routes.mjs`, run 6.5 m/s): no existing trip longer
  than before M5, new trips at most 60 s running;
- `visibility.cell_sees` gives each new cell a set with its neighbours.

Pure Python (the travel check runs `node`; it is skipped without it).
"""

import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.path.dirname(HERE)
REPO = os.path.abspath(os.path.join(BLENDER, "..", ".."))
sys.path[:0] = [BLENDER]

import layout_lib as L  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import schema as S  # noqa: E402
from castle_kit import visibility as V  # noqa: E402

LAYOUT_PATH = os.path.join(REPO, "data", "world", "layout.json")
PROPS_PATH = os.path.join(REPO, "data", "world", "props.json")
KIT_PATH = os.path.join(REPO, "data", "world", "castle_kit.json")
ROUTES = os.path.join(BLENDER, "scale_pass", "routes.mjs")

RUN = 6.5  # m/s (sim-core MoveParams), as routes.mjs
TRIP_MAX_S = 60.0
DOOR_MIN_WIDTH = 3.0
DOOR_MIN_CLEAR = 4.5

# Travel from the first spawn before M5 (main at f97cc05), metres
# (`routes.mjs --json`). No M5 layout commit may make one of these longer.
TRIPS_BEFORE_M5 = {
    "Owl Exchange counter": 11.0,
    "Great Hall dais": 85.2,
    "Grand Staircase foot": 62.6,
    "Transfiguration door (L1)": 46.9,
    "Potions cauldron (L-1)": 73.4,
    "Common room B door (L-1)": 80.8,
    "Common room D door (L-2)": 72.5,
    "Courtyard gate": 59.0,
    # Moved the shore down the new 200 m grounds (data/world/terrain/grounds.json):
    # 219 m from the spawn, 34 s at a run (178.0 on the old 120 m greybox).
    "Lake shore": 219.5,
    "Charms classroom door (L3)": 165.2,
    "Grand Staircase top (L7)": 252.1,
    "Common room C door (L6)": 233.9,
    "Common room A door (L7)": 262.5,
}

_CACHE: dict = {}


def cached(key, fn):
    if key not in _CACHE:
        _CACHE[key] = fn()
    return _CACHE[key]


def layout() -> L.Layout:
    return cached("layout", lambda: L.load_layout(LAYOUT_PATH))


def report() -> L.Report:
    return cached("report", lambda: L.validate(layout()))


def reachable() -> set[str]:
    def run():
        lay = layout()
        starts = [L.spawn_surface(lay, sp["pos"]).id for sp in lay.spawns]
        return L.reachable(lay, starts)
    return cached("reachable", run)


def adjacency() -> dict[str, list[str]]:
    return cached("adjacency", lambda: L.adjacency(layout()))


def sees() -> dict:
    return cached("sees", lambda: V.cell_sees(layout()))


def kit():
    def run():
        with open(KIT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return S.resolve(data, layout(), PROPS_PATH)
    return cached("kit", run)


def trips():
    def run():
        node = shutil.which("node")
        if node is None:
            return None
        out = subprocess.run([node, ROUTES, LAYOUT_PATH, "--json"], capture_output=True, text=True, check=True)
        return json.loads(out.stdout)
    return cached("trips", run)


def props() -> list[dict]:
    def run():
        with open(PROPS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return cached("props", run)


def air(s: L.Surface) -> tuple[float, float]:
    """A storey's range: its floor slab up to its ceiling (unroofed: the sky)."""
    return s.floor_min - G.SLAB, s.ceil_y


def plan_overlap(a: L.Surface, b: L.Surface) -> bool:
    return min(a.x1, b.x1) - max(a.x0, b.x0) > L.EPS and min(a.z1, b.z1) - max(a.z0, b.z0) > L.EPS


class SpaceChecks:
    """The checks every new space runs (mixed into one TestCase per space)."""

    # id -> (kind, cell, (x0, z0), (x1, z1), y, ceiling); a stair's y is
    # (y, y_top, rise)
    SURFACES: dict = {}
    # door id -> {joined surface id: contact width}
    DOORS: dict = {}
    # landmark ids this space adds
    LANDMARKS: tuple = ()
    # trip labels this space adds to routes.mjs
    NEW_TRIPS: tuple = ()
    # cell -> cells its sees must include
    SEES: dict = {}
    # kitted cells whose windows must still open outside
    WINDOW_CELLS: tuple = ()

    def test_surfaces_as_planned(self):
        lay = layout()
        for sid, (kind, cell, mn, mx, y, ceiling) in self.SURFACES.items():
            s = lay.by_id(sid)
            self.assertEqual((s.kind, s.cell), (kind, cell), sid)
            self.assertEqual(((s.x0, s.z0), (s.x1, s.z1)), (mn, mx), sid)
            if kind == "stair":
                self.assertEqual(((s.y, s.y_top, s.rise), s.ceiling), (y, ceiling), sid)
            else:
                self.assertEqual((s.y, s.ceiling), (y, ceiling), sid)
                self.assertFalse(s.sloped, sid)

    def test_layout_check_is_clean_and_reaches_the_space(self):
        rep = report()
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.warnings, [])
        missing = sorted(set(self.SURFACES) - reachable())
        self.assertEqual(missing, [], "not reachable from the spawns")

    def test_doors_join_the_planned_surfaces_wide_and_tall_enough(self):
        lay = layout()
        adj = adjacency()
        for did, joins in self.DOORS.items():
            door = lay.by_id(did)
            self.assertEqual(door.kind, "door", did)
            self.assertEqual(sorted(adj[did]), sorted(joins), f"{did} joins")
            # A door joins two cells (the client's visibility sets rely on it).
            self.assertGreaterEqual(len({lay.by_id(n).cell for n in adj[did]}), 2, f"{did} joins one cell")
            widths = set()
            for sid, width in joins.items():
                other = lay.by_id(sid)
                w = L.contact_width(door, other)
                self.assertAlmostEqual(w, width, places=6, msg=f"{did} / {sid}")
                self.assertGreaterEqual(w, DOOR_MIN_WIDTH, f"{did} / {sid}")
                # The rooms either side leave the door's clear height.
                self.assertGreaterEqual(other.ceil_y - door.y, door.ceiling - 1e-6, f"{did} under {sid}'s ceiling")
                widths.add(round(w, 6))
            self.assertEqual(len(widths), 1, f"{did}: one opening width both sides")
            self.assertGreaterEqual(door.ceiling, DOOR_MIN_CLEAR, did)
            self.assertIn((widths.pop(), door.ceiling), G.ARCH_SIZES, f"{did}: an arch the catalogue has")

    def test_no_overlap_in_3d(self):
        lay = layout()
        for sid in self.SURFACES:
            a = lay.by_id(sid)
            a0, a1 = air(a)
            for b in lay.surfaces:
                if b.id == a.id or not plan_overlap(a, b):
                    continue
                b0, b1 = air(b)
                self.assertFalse(a0 < b1 - L.EPS and b0 < a1 - L.EPS,
                                 f"{sid} overlaps {b.id}: {a0:.2f}..{a1:.2f} against {b0:.2f}..{b1:.2f}")

    def test_kit_resolves_and_windows_open_outside(self):
        plan, rep = kit()
        self.assertEqual(rep.errors, [])
        lay = layout()
        for cell in self.WINDOW_CELLS:
            cp = plan.cells.get(cell) or plan.planned.get(cell)
            self.assertIsNotNone(cp, cell)
            runs = {r.id: r for r in cp.runs}
            windows = [o for o in cp.openings if o.kind == "window" and o.run is not None]
            self.assertTrue(windows, cell)
            for o in windows:
                mass = G.wall_mass(lay, runs[o.run], o.t0, o.t1, o.y0, o.y1)
                self.assertTrue(mass.outside, f"{o.id} would open into {list(mass.behind)}")

    def test_landmarks_on_walkable_ground(self):
        lay = layout()
        marks = {m["id"]: m for m in lay.landmarks}
        for lid in self.LANDMARKS:
            self.assertIn(lid, marks)
            s = L.spawn_surface(lay, marks[lid]["pos"])
            self.assertIsNotNone(s, f"{lid} stands on no surface")
            self.assertNotEqual(s.kind, "door", lid)
            self.assertIn(s.id, reachable(), lid)

    def test_travel(self):
        got = trips()
        if got is None:
            self.skipTest("node not found")
        for label, before in TRIPS_BEFORE_M5.items():
            self.assertIsNotNone(got.get(label), label)
            self.assertLessEqual(got[label], before + 0.05, f"{label}: longer than before M5")
        for label in self.NEW_TRIPS:
            self.assertIsNotNone(got.get(label), label)
            self.assertLessEqual(got[label] / RUN, TRIP_MAX_S, label)

    def test_sees(self):
        vis = sees()
        cells = {c for (_, c, *_rest) in self.SURFACES.values()}
        for cell in cells:
            self.assertIsInstance(vis[cell], list, cell)
            self.assertIn(cell, vis[cell])
        for cell, want in self.SEES.items():
            self.assertTrue(set(want) <= set(vis[cell]), f"{cell} sees {vis[cell]}")


class N3WestTowerCrossings(SpaceChecks, unittest.TestCase):
    """N3: the west tower's crossings on L4-L6. A vestibule at each corridor's
    west end (x -92..-84, z -4..7) and a 4 x 5 door onto the tower's corner
    landing (wt_ne1, wt_ne2, wt_top), opening the three dead ends."""

    SURFACES = {
        "l4_vestibule": ("corridor", "l4_corridor_west", (-92, -4), (-84, 7), 36, 8.4),
        "l5_vestibule": ("corridor", "l5_corridor_west", (-92, -4), (-84, 7), 45, 8.4),
        "l6_vestibule": ("corridor", "l6_corridor_west", (-92, -4), (-84, 7), 54, 8.4),
        "wt_door_l4": ("door", "west_tower_stair", (-94, -4), (-92, 0), 36, 5),
        "wt_door_l5": ("door", "west_tower_stair", (-94, -4), (-92, 0), 45, 5),
        "wt_door_l6": ("door", "west_tower_stair", (-94, -4), (-92, 0), 54, 5),
    }
    DOORS = {
        "wt_door_l4": {"wt_ne1": 4.0, "l4_vestibule": 4.0},
        "wt_door_l5": {"wt_ne2": 4.0, "l5_vestibule": 4.0},
        "wt_door_l6": {"wt_top": 4.0, "l6_vestibule": 4.0},
    }
    NEW_TRIPS = ("West tower, sixth-floor door (L6)",)
    SEES = {
        "west_tower_stair": ("l4_corridor_west", "l5_corridor_west", "l6_corridor_west", "tf_corridor_west"),
        "l4_corridor_west": ("west_tower_stair",),
        "l5_corridor_west": ("west_tower_stair",),
        "l6_corridor_west": ("west_tower_stair", "common_room_house_c"),
    }

    def test_each_vestibule_continues_its_corridor(self):
        lay = layout()
        for lv in (4, 5, 6):
            v, c = lay.by_id(f"l{lv}_vestibule"), lay.by_id(f"l{lv}_corridor_west")
            self.assertIn(c.id, adjacency()[v.id])
            self.assertAlmostEqual(L.contact_width(v, c), 9.0)  # the corridor's whole west end

    def test_the_dead_end_doors_are_gone(self):
        ids = {p.get("id") for p in props()["props"]}
        for lv in (4, 5, 6):
            self.assertNotIn(f"l{lv}_end_door", ids)

    def test_the_common_room_is_reached_three_ways(self):
        # From the tower top and, through it, the L4 and L5 crossings as well
        # as the old L3 door: the tower stair joins four corridors now.
        lay = layout()
        tower = {s.id for s in lay.surfaces if s.cell == "west_tower_stair"}
        outside = {n for sid in tower for n in adjacency()[sid] if n not in tower}
        self.assertEqual(sorted(outside), ["cr_c_door", "l4_vestibule", "l5_vestibule", "l6_vestibule", "tf_corridor_west"])


class N1UndercroftWalk(SpaceChecks, unittest.TestCase):
    """N1: the Undercroft Walk (`gf_undercroft`), a ground-floor walk exactly
    under the first-floor north corridor (x -28..32, z -14..-2), from a 5 x 6
    arch in the Grand Staircase's ground-floor east wall to a 3 x 4.5 door
    in the Entrance Hall's north wall (where a niche and its armour were)."""

    SURFACES = {
        "uc_walk": ("corridor", "gf_undercroft", (-28, -14), (32, -2), 0, 8.4),
        "gs_door_l0_north": ("door", "gf_undercroft", (-30, -10), (-28, -5), 0, 6),
        "eh_north_door": ("door", "gf_undercroft", (1, -2), (4, 0), 0, 4.5),
    }
    DOORS = {
        "gs_door_l0_north": {"gs_floor_north": 5.0, "uc_walk": 5.0},
        "eh_north_door": {"eh_floor_west": 3.0, "uc_walk": 3.0},
    }
    LANDMARKS = ("undercroft_well",)
    NEW_TRIPS = ("Undercroft Well (L0)",)
    SEES = {
        "gf_undercroft": ("entrance_hall", "grand_staircase", "gf_corridor_west"),
        "entrance_hall": ("gf_undercroft",),
        "grand_staircase": ("gf_undercroft",),
    }
    # The first-floor corridor's windows stand over the walk (its north wall's n0).
    WINDOW_CELLS = ("ff_corridor_north", "gf_corridor_west", "entrance_hall")

    def test_it_lies_under_the_first_floor_corridor(self):
        lay = layout()
        walk, ff = lay.by_id("uc_walk"), lay.by_id("ff_corridor_north")
        self.assertTrue(ff.x0 <= walk.x0 and walk.x1 <= ff.x1 and (ff.z0, ff.z1) == (walk.z0, walk.z1))
        self.assertAlmostEqual(walk.ceil_y + G.SLAB, ff.y)  # its roof slab is the corridor's floor

    def test_a_second_ground_floor_route_between_the_hall_and_the_staircase(self):
        # Without the west corridor the hall still reaches the staircase on the ground floor.
        lay = layout()
        adj = adjacency()
        ground = {s.id for s in lay.surfaces if s.level == 0 and s.floor_max <= 0.0 + L.EPS and s.cell != "gf_corridor_west"}
        seen, todo = {"eh_floor_west"}, ["eh_floor_west"]
        while todo:
            for n in adj[todo.pop()]:
                if n in ground and n not in seen:
                    seen.add(n)
                    todo.append(n)
        self.assertIn("gs_floor_north", seen)

    def test_the_kitted_neighbours_arch_the_doors_and_drop_the_niche(self):
        plan, _ = kit()
        arches = {(o.cell, o.door): o for cp in plan.cells.values() for o in cp.openings if o.kind == "arch"}
        self.assertEqual(arches[("entrance_hall", "eh_north_door")].piece, "kit_stone_arch_3x4.5")
        self.assertEqual(arches[("grand_staircase", "gs_door_l0_north")].piece, "kit_stone_arch_5x6")
        eh = plan.cells["entrance_hall"]
        self.assertNotIn("armour_n_w", {h.id for h in eh.hero})
        self.assertFalse([o for o in eh.openings if o.kind == "niche" and o.run and o.run.startswith("eh_floor_west:n")])


class N7LongGallery(SpaceChecks, unittest.TestCase):
    """N7: the Long Gallery (`ff_gallery_east`), a first-floor gallery 54 m
    long (x 42..96, z -14..-4) 13 m north of the Great Hall, through a 5 x 6
    arch in the first-floor north corridor's east wall, which loses its two
    end windows."""

    SURFACES = {
        "ff_gallery_east": ("corridor", "ff_gallery_east", (42, -14), (96, -4), 9, 8.4),
        "ff_gallery_arch": ("door", "ff_gallery_east", (40, -11.5), (42, -6.5), 9, 6),
    }
    DOORS = {
        "ff_gallery_arch": {"ff_corridor_north": 5.0, "ff_gallery_east": 5.0},
    }
    LANDMARKS = ("gallery_rose_window",)
    NEW_TRIPS = ("Long Gallery, rose window (L1)",)
    SEES = {
        "ff_gallery_east": ("ff_corridor_north", "grand_staircase", "entrance_hall"),
        "ff_corridor_north": ("ff_gallery_east",),
    }
    # The corridor's remaining windows and the Great Hall's lancets (north wall 13 m away).
    WINDOW_CELLS = ("ff_corridor_north", "great_hall")

    def test_the_gallery_stands_clear_of_the_great_hall(self):
        lay = layout()
        gal = lay.by_id("ff_gallery_east")
        hall = [s for s in lay.surfaces if s.cell == "great_hall"]
        self.assertEqual(min(s.z0 for s in hall) - gal.z1, 13.0)

    def test_the_corridor_end_wall_loses_its_windows_and_takes_the_arch(self):
        plan, _ = kit()
        cp = plan.cells["ff_corridor_north"]
        arch = next(o for o in cp.openings if o.kind == "arch" and o.door == "ff_gallery_arch")
        self.assertEqual((arch.piece, arch.leaves), ("kit_stone_arch_5x6", False))
        east = [o for o in cp.openings if o.kind != "arch" and o.run and o.run.startswith("ff_corridor_north:e")]
        self.assertEqual(east, [])

    def test_the_rose_window_is_at_the_east_end(self):
        lay = layout()
        mark = next(m for m in lay.landmarks if m["id"] == "gallery_rose_window")
        self.assertEqual(L.spawn_surface(lay, mark["pos"]).id, "ff_gallery_east")
        self.assertGreaterEqual(mark["pos"][0], lay.by_id("ff_gallery_east").x1 - 4)


class N2TrophyRoom(SpaceChecks, unittest.TestCase):
    """N2: the Trophy Room (`trophy_room`, castle M5b), 20 x 14 m (x -6..14,
    z -30..-16) off the Undercroft Walk's north wall, through a 5 x 6 arch
    in the walk's bay 5 (x 2..7), which the M5a kit left plain for it."""

    SURFACES = {
        "trophy_room": ("room", "trophy_room", (-6, -30), (14, -16), 0, 8.4),
        "trophy_door": ("door", "trophy_room", (2, -16), (7, -14), 0, 6),
    }
    DOORS = {
        "trophy_door": {"uc_walk": 5.0, "trophy_room": 5.0},
    }
    LANDMARKS = ("trophy_armillary",)
    NEW_TRIPS = ("Trophy Room, armillary (L0)",)
    SEES = {
        "trophy_room": ("gf_undercroft", "entrance_hall", "grand_staircase"),
        "gf_undercroft": ("trophy_room",),
    }
    # The first-floor corridor's north windows stand over the room's roof; the
    # walk's own north lancets (bays 0, 1 and its east end) stay clear of it.
    WINDOW_CELLS = ("ff_corridor_north", "gf_undercroft")

    def test_it_stands_beside_the_walk_under_the_first_floor_windows(self):
        lay = layout()
        room, walk, ff = lay.by_id("trophy_room"), lay.by_id("uc_walk"), lay.by_id("ff_corridor_north")
        self.assertEqual(walk.z0 - room.z1, 2.0)  # one wall between them, the door's depth
        self.assertTrue(walk.x0 <= room.x0 and room.x1 <= walk.x1)
        self.assertLessEqual(room.ceil_y + G.SLAB, ff.y)  # its roof stays under the corridor's floor (and its sills)

    def test_the_undercroft_arches_the_door_and_keeps_its_bays(self):
        plan, _ = kit()
        cp = plan.cells["gf_undercroft"]
        arch = next(o for o in cp.openings if o.kind == "arch" and o.door == "trophy_door")
        self.assertEqual((arch.piece, arch.leaves), ("kit_stone_arch_5x6", False))
        north = [o for o in cp.openings if o.kind != "arch" and o.run and o.run.startswith("uc_walk:n")]
        # The niches (and their statues) and the lancets where M5a put them.
        self.assertEqual(sorted(o.centre for o in north if o.kind == "niche"), [-13.0, -7.0, -1.0, 11.0, 17.0])
        self.assertEqual(sorted(o.centre for o in north if o.kind == "window"), [-25.0, -19.0, 23.0, 29.0])
        statues = sorted(h.pos[0] for h in cp.hero if h.piece.startswith("kit_statue_robed"))
        self.assertEqual(statues, [-13, -7, -1, 11, 17])


class N4SecondFloorLibrary(SpaceChecks, unittest.TestCase):
    """N4: the Second-Floor Library (`sf_library`, castle M5b), 18 x 20 m
    (x -112..-94, z -4..16) under the west tower's third-floor floor, through
    a 5 x 6 arch where the second-floor west corridor ended at a blind door."""

    SURFACES = {
        "sf_library": ("room", "sf_library", (-112, -4), (-94, 16), 18, 8.4),
        "sf_library_door": ("door", "sf_library", (-94, 0), (-92, 5), 18, 6),
    }
    DOORS = {
        "sf_library_door": {"sf_corridor_west": 5.0, "sf_library": 5.0},
    }
    LANDMARKS = ("library_floor_map",)
    NEW_TRIPS = ("Second-Floor Library, floor map (L2)",)
    SEES = {
        "sf_library": ("sf_corridor_west", "grand_staircase", "grand_staircase_upper"),
        "sf_corridor_west": ("sf_library",),
    }
    # The corridor's lancets and the tower's slits (above the library's roof).
    WINDOW_CELLS = ("sf_corridor_west", "tf_corridor_west", "west_tower_stair")

    def test_it_stands_under_the_tower(self):
        lay = layout()
        lib = lay.by_id("sf_library")
        tower = [s for s in lay.surfaces if s.cell == "west_tower_stair" and s.kind != "door"]
        self.assertAlmostEqual(lib.ceil_y + G.SLAB, min(s.floor_min for s in tower))  # its roof is the tower's floor
        for s in tower:
            self.assertTrue(lib.x0 <= s.x0 and s.x1 <= lib.x1 and lib.z0 <= s.z0 and s.z1 <= lib.z1, s.id)

    def test_the_corridor_end_opens_into_the_library(self):
        plan, _ = kit()
        cp = plan.cells["sf_corridor_west"]
        arch = next(o for o in cp.openings if o.kind == "arch" and o.door == "sf_library_door")
        self.assertEqual((arch.piece, arch.leaves), ("kit_stone_arch_5x6", False))
        self.assertEqual([o for o in cp.openings if o.kind == "blind_door"], [])
        # The loose-stone tell keeps clear of the arch's quoins (on the south wall now).
        tell = next(k for k in props()["kit"] if k["id"] == "tell_sf_corridor_end")
        self.assertGreaterEqual(tell["pos"][2], 5 + G.ARCH_SURROUND + G.TELL_HALF_WIDTH)


class N6Kitchens(SpaceChecks, unittest.TestCase):
    """N6: the Kitchens (`kitchens`, castle M5b), 22 x 12 m (x 36..58,
    z 33..45, y -9) south of the kitchen corridor, under the courtyard's
    edge, through the fruit-bowl painting: the painting is now the door
    (4 x 5, x 45.5..49.5, in the corridor's south wall), as the guardian
    portrait is common room A's."""

    SURFACES = {
        "kitchens": ("room", "kitchens", (36, 33), (58, 45), -9, 8.4),
        "kitchen_painting_door": ("door", "kitchens", (45.5, 31), (49.5, 33), -9, 5),
    }
    DOORS = {
        "kitchen_painting_door": {"kitchen_corridor": 4.0, "kitchens": 4.0},
    }
    LANDMARKS = ("kitchens_tables", "kitchen_portrait")
    NEW_TRIPS = ("Kitchens (L-1)",)
    SEES = {
        "kitchens": ("basement_east", "entrance_hall"),
        "basement_east": ("kitchens",),
    }
    # The Great Hall's south lancets stand over the kitchens' roof line.
    WINDOW_CELLS = ("great_hall",)

    def test_its_roof_is_the_ground(self):
        lay = layout()
        k = lay.by_id("kitchens")
        self.assertAlmostEqual(k.ceil_y + G.SLAB, 0.0)
        court = lay.by_id("courtyard")
        self.assertTrue(plan_overlap(k, court))  # under the courtyard's edge
        hall = [s for s in lay.surfaces if s.cell == "great_hall"]
        self.assertGreaterEqual(k.z0, max(s.z1 for s in hall))  # south of the Great Hall's line

    def test_the_painting_is_the_door(self):
        lay = layout()
        door = lay.by_id("kitchen_painting_door")
        mark = next(m for m in lay.landmarks if m["id"] == "kitchen_portrait")
        self.assertEqual(L.spawn_surface(lay, mark["pos"]).id, "kitchen_corridor")
        self.assertTrue(door.x0 <= mark["pos"][0] <= door.x1)
        boxes = {p["id"]: p for p in props()["props"]}
        canvas = boxes["kc_painting"]
        self.assertEqual((canvas["min"][0], canvas["max"][0]), (door.x0, door.x1))  # it fills the opening
        self.assertEqual(canvas["max"][2], door.z0)  # on the corridor's wall face
        self.assertAlmostEqual(canvas["max"][1] - canvas["min"][1], door.ceiling)
        self.assertNotIn("kc_painting_frame", boxes)  # gone from the north wall


class X1SculleryPassage(SpaceChecks, unittest.TestCase):
    """Extra loop 1 (castle M5b): the Scullery Passage, a
    20 m passage (x 14..34, z 37..43, y -9) under the Entrance Hall from the
    dungeon corridor's east wall to the kitchens' west wall, 4 x 5 doors at
    both ends, in its own cell `scullery_passage` (a corridor). The two basement stairs from the hall now join in a loop:
    dungeon steps, the passage, the kitchens, the painting, the kitchen
    corridor, the kitchen steps."""

    SURFACES = {
        "scullery_door_west": ("door", "scullery_passage", (12, 38), (14, 42), -9, 5),
        "scullery_passage": ("corridor", "scullery_passage", (14, 37), (34, 43), -9, 8.4),
        "scullery_door_east": ("door", "scullery_passage", (34, 38), (36, 42), -9, 5),
    }
    DOORS = {
        "scullery_door_west": {"dungeon_corridor": 4.0, "scullery_passage": 4.0},
        "scullery_door_east": {"scullery_passage": 4.0, "kitchens": 4.0},
    }
    NEW_TRIPS = ("Scullery Passage (L-1)",)
    SEES = {
        "scullery_passage": ("dungeon_west", "kitchens"),
        "kitchens": ("scullery_passage", "dungeon_west"),
        "dungeon_west": ("scullery_passage", "kitchens"),
    }
    WINDOW_CELLS = ("great_hall",)

    def test_the_basement_stairs_join_in_a_loop(self):
        # Without the Entrance Hall the dungeon steps still reach the kitchen steps.
        lay = layout()
        adj = adjacency()
        below = {s.id for s in lay.surfaces if s.floor_min < -0.5 and s.house is None}
        seen, todo = {"eh_dungeon_steps"}, ["eh_dungeon_steps"]
        while todo:
            for n in adj[todo.pop()]:
                if n in below and n not in seen:
                    seen.add(n)
                    todo.append(n)
        self.assertIn("eh_kitchen_steps", seen)

    def test_it_runs_under_the_entrance_hall(self):
        lay = layout()
        p = lay.by_id("scullery_passage")
        self.assertAlmostEqual(p.ceil_y + G.SLAB, 0.0)
        self.assertTrue(plan_overlap(p, lay.by_id("eh_floor_south")))

    def test_the_dungeon_chains_keep_clear_of_the_door(self):
        door = layout().by_id("scullery_door_west")
        chains = [p for p in L.load_props(PROPS_PATH) if p.id.startswith("dc_chain")]
        self.assertEqual(len(chains), 2)
        for c in chains:
            self.assertTrue(c.max[2] <= door.z0 - G.ARCH_SURROUND or c.min[2] >= door.z1 + G.ARCH_SURROUND, c.id)


class X2TrophyStair(SpaceChecks, unittest.TestCase):
    """Extra loop 2 (castle M5b): the Trophy Stair, the Long
    Gallery's second way out. A 3 x 4.5 door in the gallery's north wall
    (bay 0) onto a landing, a straight 16 m flight down west in a tall
    stairwell and a short passage to a 3 x 4.5 door in the Trophy Room's
    east wall, its own cell `trophy_stair`. The hall, the Undercroft Walk,
    the Trophy Room, the stair, the gallery, the first-floor corridor and
    the Marble Staircase now make a loop."""

    SURFACES = {
        "trophy_east_door": ("door", "trophy_stair", (14, -22), (16, -19), 0, 4.5),
        "trophy_passage": ("corridor", "trophy_stair", (16, -23), (26, -18), 0, 8.4),
        "trophy_stair": ("stair", "trophy_stair", (26, -23), (42, -18), (0, 9, "+x"), 17.4),
        "trophy_stair_landing": ("corridor", "trophy_stair", (42, -23), (48, -16), 9, 8.4),
        "gallery_north_door": ("door", "trophy_stair", (43.5, -16), (46.5, -14), 9, 4.5),
    }
    DOORS = {
        "trophy_east_door": {"trophy_room": 3.0, "trophy_passage": 3.0},
        "gallery_north_door": {"trophy_stair_landing": 3.0, "ff_gallery_east": 3.0},
    }
    NEW_TRIPS = ("Trophy Stair, gallery landing (L1)",)
    SEES = {
        "trophy_stair": ("trophy_room", "ff_gallery_east", "gf_undercroft", "ff_corridor_north"),
        "trophy_room": ("trophy_stair", "ff_gallery_east"),
        "ff_gallery_east": ("trophy_stair", "trophy_room"),
    }
    # The stairwell keeps more than a wall mass (3.2 m) from the first-floor
    # corridor's and the walk's north lancets; the gallery keeps its own.
    WINDOW_CELLS = ("ff_corridor_north", "gf_undercroft", "ff_gallery_east")

    def test_the_gallery_and_the_trophy_room_make_a_loop(self):
        # Without the gallery's arch the gallery still reaches the Undercroft Walk.
        lay = layout()
        adj = adjacency()
        seen, todo = {"ff_gallery_east"}, ["ff_gallery_east"]
        while todo:
            for n in adj[todo.pop()]:
                if n != "ff_gallery_arch" and n not in seen:
                    seen.add(n)
                    todo.append(n)
        self.assertIn("uc_walk", seen)

    def test_the_flight_is_as_easy_as_the_grand_staircase(self):
        st = layout().by_id("trophy_stair")
        self.assertLessEqual((st.y_top - st.y) / (st.x1 - st.x0), 9 / 16 + 1e-9)  # 29 degrees, as the GS flights

    def test_the_stairwell_keeps_clear_of_the_north_lancets(self):
        lay = layout()
        for sid in ("trophy_passage", "trophy_stair"):  # the landing (x 42..48) is beyond both
            self.assertGreater(lay.by_id("uc_walk").z0 - lay.by_id(sid).z1, G.WALL_MASS_MAX, sid)

    def test_the_gallery_keeps_its_statues_and_lancets(self):
        plan, _ = kit()
        cp = plan.cells["ff_gallery_east"]
        arch = next(o for o in cp.openings if o.kind == "arch" and o.door == "gallery_north_door")
        self.assertEqual((arch.piece, arch.leaves), ("kit_stone_arch_3x4.5", False))
        north = [o for o in cp.openings if o.kind != "arch" and o.run and o.run.startswith("ff_gallery_east:n")]
        self.assertEqual(sorted(o.centre for o in north if o.kind == "niche"), [51.0, 63.0, 75.0, 87.0])
        self.assertEqual(sorted(o.centre for o in north if o.kind == "window"), [57.0, 69.0, 81.0, 93.0])
        statues = sorted(h.pos[0] for h in cp.hero if h.piece.startswith("kit_statue_robed"))
        self.assertEqual(statues, [51, 63, 75, 87])


class N5OwlGate(SpaceChecks, unittest.TestCase):
    """N5 (castle M5c): the Owl Gate, a 3 x 4.5 door (x 1..4,
    z 40..42) in the Entrance Hall's south wall at the Owl Exchange court,
    into the courtyard's north-west corner, the Exchange Yard (landmark
    `exchange_yard`). The hall's second way out to the courtyard, clear of
    the front doors' open leaves (x 7.2..11.2 on the same wall face)."""

    SURFACES = {
        "eh_owl_gate": ("door", "entrance_hall", (1, 40), (4, 42), 0, 4.5),
    }
    DOORS = {
        "eh_owl_gate": {"eh_floor_west": 3.0, "courtyard": 3.0},
    }
    LANDMARKS = ("exchange_yard",)
    NEW_TRIPS = ("Exchange Yard, by the Owl Gate (L0)",)
    SEES = {
        "entrance_hall": ("courtyard",),
    }
    # The hall's lancets (the traceried window over the front doors has no run).
    WINDOW_CELLS = ("entrance_hall",)

    def test_a_second_way_out_to_the_courtyard(self):
        # Without the front doors the hall still reaches the courtyard on the ground floor.
        lay = layout()
        adj = adjacency()
        seen, todo = {"eh_floor_west"}, ["eh_floor_west"]
        while todo:
            for n in adj[todo.pop()]:
                if n != "eh_front_doors" and lay.by_id(n).cell in ("entrance_hall", "courtyard") and n not in seen:
                    seen.add(n)
                    todo.append(n)
        self.assertIn("courtyard", seen)

    def test_the_gate_keeps_clear_of_the_front_door_leaves(self):
        lay = layout()
        gate, doors = lay.by_id("eh_owl_gate"), lay.by_id("eh_front_doors")
        self.assertEqual((gate.z0, gate.z1), (doors.z0, doors.z1))  # the same wall
        leaf_x0 = doors.x0 - G.ARCH_SURROUND - G.leaf_width(doors.x1 - doors.x0)  # the west leaf, flat on the wall
        self.assertAlmostEqual(leaf_x0, 7.2)
        self.assertLessEqual(gate.x1 + G.ARCH_SURROUND, leaf_x0 - G.LEAF_CLEAR)

    def test_the_hall_arches_the_gate_and_splits_its_south_wall(self):
        plan, _ = kit()
        cp = plan.cells["entrance_hall"]
        arch = next(o for o in cp.openings if o.kind == "arch" and o.door == "eh_owl_gate")
        self.assertEqual((arch.piece, arch.leaves), ("kit_stone_arch_3x4.5", False))
        south = sorted((r.t0, r.t1) for r in cp.runs if r.id.startswith("eh_floor_west:s"))
        self.assertEqual(south, [(0.0, 1.0), (4.0, 5.0)])
        # The court's furniture where M2 put it: the counter, the perches, the lanterns.
        heroes = {h.id: h for h in cp.hero}
        self.assertEqual(heroes["owl_counter"].pos, (0, 0, 30))
        self.assertEqual(heroes["owl_perch_s"].pos, (0, 0, 37.6))

    def test_the_corner_tell_stays_on_the_west_wall_clear_of_the_gate(self):
        tell = next(k for k in props()["kit"] if k["id"] == "tell_eh_front_corner")
        gate = layout().by_id("eh_owl_gate")
        self.assertAlmostEqual(tell["pos"][0], 0.0, places=2)  # on the west wall face, not the south wall
        self.assertLessEqual(tell["pos"][2] + G.TELL_HALF_WIDTH, gate.z0 - 0.5)

    def test_the_exchange_yard_is_the_courtyards_north_west_corner(self):
        lay = layout()
        mark = next(m for m in lay.landmarks if m["id"] == "exchange_yard")
        court, doors, gate = lay.by_id("courtyard"), lay.by_id("eh_front_doors"), lay.by_id("eh_owl_gate")
        self.assertEqual(L.spawn_surface(lay, mark["pos"]).id, "courtyard")
        self.assertLess(mark["pos"][0], doors.x0)  # west of the front doors' axis
        self.assertLess(mark["pos"][2], (court.z0 + court.z1) / 2)  # the north half
        # Nearer the gate than the front doors.
        dg = abs(mark["pos"][0] - (gate.x0 + gate.x1) / 2) + abs(mark["pos"][2] - gate.z1)
        dd = abs(mark["pos"][0] - (doors.x0 + doors.x1) / 2) + abs(mark["pos"][2] - doors.z1)
        self.assertLess(dg, dd)


if __name__ == "__main__":
    unittest.main()
