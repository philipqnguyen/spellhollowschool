"""Unit tests for the castle kit  (tools/blender/world:
kit_models.py, kit.py, find_tells.py): budgets, materials, placement in
data/world/props.json, the find-spot tells and the station dressing. Pure
Python (numpy for the atlas); run through tests/run_tests.py."""

import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.join(os.path.dirname(HERE), "world"), os.path.dirname(HERE)]

import find_tells  # noqa: E402
import kit  # noqa: E402
import kit_models as km  # noqa: E402
import layout_lib as L  # noqa: E402

LAYOUT = os.path.join(REPO, "data", "world", "layout.json")
PROPS = os.path.join(REPO, "data", "world", "props.json")
STATIONS = ("craft_charms", "craft_transfiguration", "cauldron", "owl_exchange")


class KitModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kits = {name: km.build(name) for name in km.MODELS}

    def test_budgets_and_materials(self):
        for name, k in self.kits.items():
            self.assertLessEqual(k.mesh.triangle_count(), km.budget(name), name)
            self.assertGreater(k.mesh.triangle_count(), 0, name)
            for mat in k.mesh.materials():
                if mat.startswith("g:"):
                    self.assertIn(mat[2:], L.MATERIALS, (name, mat))
                else:
                    self.assertIn(mat, kit.TILES, (name, mat))

    def test_tells_are_small_flat_and_quiet(self):
        for name, k in self.kits.items():
            if not name.startswith("tell_"):
                continue
            self.assertTrue(all(m.startswith("g:") for m in k.mesh.materials()), name)
            lo, hi = k.mesh.bounds()
            self.assertLessEqual(max(hi[i] - lo[i] for i in range(3)), 0.7, name)
            self.assertEqual(k.lights, [], name)

    def test_wall_pieces_stay_in_front_of_their_wall(self):
        for name in ("owl_exchange_counter", "knitting_corner", "transfiguration_table", "potions_shelves",
                     "potions_blackboard", "apothecary_cupboard", "dungeon_wall", "wall_torch", "tell_loose_stone",
                     "tell_tapestry_corner"):
            lo, _ = self.kits[name].mesh.bounds()
            self.assertGreaterEqual(lo[2], -1e-6, name)
            self.assertGreaterEqual(lo[1], -1e-6, name)

    def test_sized_pieces_take_their_size(self):
        wall = km.build("dungeon_wall", width=3.5, height=5.4).mesh
        lo, hi = wall.bounds()
        self.assertAlmostEqual(hi[0] - lo[0], 3.5, places=6)
        floor = km.build("dungeon_floor", width=15.5, depth=16.0).mesh
        lo, hi = floor.bounds()
        self.assertAlmostEqual(hi[2] - lo[2], 16.0, places=6)

    def test_light_lists_are_well_formed(self):
        for name, k in self.kits.items():
            for lt in k.lights:
                self.assertEqual(len(lt["pos"]), 3, name)
                self.assertRegex(lt["color"], r"^#[0-9A-Fa-f]{6}$")
                self.assertGreater(lt["intensity"], 0)
                self.assertGreater(lt["range"], 0)


class KitPlacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layout = L.load_layout(LAYOUT)
        cls.props = kit.load_kit(PROPS)
        cls.cells, cls.atlas = kit.assemble(cls.props)

    def test_kit_cells_exist_and_atlas_fits(self):
        cells = {s.cell for s in self.layout.surfaces}
        for p in self.props:
            self.assertIn(p.cell, cells, p.id)
        self.assertLessEqual(self.atlas.size, 1024)
        base, glow = self.atlas.paint()
        self.assertEqual(base.shape, (self.atlas.size, self.atlas.size, 4))
        self.assertGreater(float(glow[..., :3].max()), 0.5)  # flames and brews glow

    def test_uvs_match_faces_and_stay_in_the_atlas(self):
        for cell, ck in self.cells.items():
            self.assertEqual(len(ck.uvs), len(ck.mesh.faces), cell)
            for f, uvs in zip(ck.mesh.faces, ck.uvs):
                self.assertEqual(len(uvs), len(f.idx))
                for u, v in uvs:
                    self.assertTrue(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0, (cell, u, v))

    def test_cells_stay_inside_the_style_guide_budget(self):
        # 150,000 triangles per streaming cell (style guide section 6).
        for cell, ck in self.cells.items():
            flat = sum(len(p) - 2 for polys in ck.flats.values() for p in polys)
            self.assertLessEqual(ck.mesh.triangle_count() + flat, 150_000, cell)

    def test_every_station_is_dressed_and_its_standing_spot_stays_clear(self):
        by_id = {p.id: p for p in self.props}
        stations = [it for it in self.layout.interactables if it.kind in STATIONS]
        self.assertTrue(stations)
        for it in stations:
            # Dressed within the station's reach (at least 3.2 m: the Owl
            # Exchange's long counter is served from 3.5 m).
            near = max(3.2, it.radius)
            dressed = [p for p in self.props if math.dist((p.pos[0], p.pos[2]), (it.pos[0], it.pos[2])) <= near
                       and p.model in ("owl_exchange_counter", "knitting_corner", "transfiguration_table",
                                       "classroom_cauldron")]
            self.assertTrue(dressed, it.id)
            if it.kind != "cauldron":
                # Wall stations stand back from the spot the player uses.
                for p in dressed:
                    self.assertGreaterEqual(math.dist((p.pos[0], p.pos[2]), (it.pos[0], it.pos[2])), 0.9, it.id)
        self.assertIn("owl_exchange_counter", by_id)

    def test_every_find_spot_has_a_tell_nearby(self):
        tells = [p for p in self.props if p.model.startswith("tell_")]
        spots = [it for it in self.layout.interactables if it.kind == "find_spot"]
        self.assertEqual(len(tells), len(spots))
        for it in spots:
            near = [t for t in tells if math.dist(t.pos, it.pos) <= find_tells.WALL_REACH + 0.6]
            self.assertTrue(near, it.id)

    def test_tells_are_up_to_date(self):
        with open(PROPS, encoding="utf-8") as f:
            current = f.read()
        self.assertEqual(find_tells.generate(current), current,
                         "data/world/props.json: run tools/blender/world/find_tells.py -- --write")


if __name__ == "__main__":
    unittest.main()
