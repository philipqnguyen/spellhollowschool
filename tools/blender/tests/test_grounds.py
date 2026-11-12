"""The grounds' pieces, terrain, dressing, foliage, water and
backdrop, and their lint (docs/art/castle-kit.md "Budgets", "Exterior"). Pure Python (the
export, world/grounds_export.py, turns the same geometry into Blender objects)."""

import json
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [os.path.dirname(HERE), os.path.join(os.path.dirname(HERE), "world")]

import foliage as FO  # noqa: E402
import grounds_backdrop as BD  # noqa: E402
import grounds_build as GB  # noqa: E402
import grounds_dressing as D  # noqa: E402
import grounds_terrain as GT  # noqa: E402
import layout_lib as L  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import pieces as P  # noqa: E402
from castle_kit.pieces import grounds as GP  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
LAYOUT = os.path.join(ROOT, "data", "world", "layout.json")
TERRAIN = os.path.join(ROOT, "data", "world", "terrain")
TEXTURES = os.path.join(ROOT, "data", "art", "castle_textures.json")
TOON = os.path.join(ROOT, "data", "art", "toon_palette.json")
KEEP_OUT = os.path.join(ROOT, "data", "world", "keep_out.json")

_BUILT = {}


def built():
    """The whole grounds build, once per test run."""
    if "gr" not in _BUILT:
        layout = L.load_layout(LAYOUT)
        with open(TEXTURES, encoding="utf-8") as f:
            textures = json.load(f)
        _BUILT["layout"] = layout
        _BUILT["gr"] = GB.build(layout, TERRAIN, textures)
    return _BUILT["gr"]


class GroundsPieceTests(unittest.TestCase):
    """pieces/grounds.py: caps, frames, determinism (PieceTests and PaletteRuleTests
    cover the face tags, the convexity and the palette for every catalogue piece)."""

    def test_registered_and_within_caps(self):
        caps = {"kit_grounds_hut": GP.HUT_TRIS, "kit_woodpile": GP.SMALL_TRIS, "kit_water_butt": GP.SMALL_TRIS,
                "kit_bench_rustic": GP.SMALL_TRIS, "kit_boathouse": GP.BOATHOUSE_TRIS, "kit_standing_stone": GP.STONE_TRIS,
                "kit_rock": GP.ROCK_TRIS,
                "kit_glasshouse_garden": GP.GLASSHOUSE_GARDEN_TRIS,
                "kit_drystone_wall": GP.WALL_TRIS_PER_M * P.CATALOGUE["kit_drystone_wall"]["length"]}
        for name, cap in caps.items():
            self.assertIn(name, P.CATALOGUE)
            p = P.build(name, **P.CATALOGUE[name])
            self.assertGreater(p.triangles(), 0, name)
            self.assertLessEqual(p.triangles(), cap, name)

    def test_deterministic(self):
        for name in GP.BUILDERS:
            a, b = P.build(name, **P.CATALOGUE[name]), P.build(name, **P.CATALOGUE[name])
            self.assertEqual([f.pts for f in a.faces], [f.pts for f in b.faces], name)

    def test_hut_door_faces_plus_z_and_stone_walls(self):
        hut = P.build("kit_grounds_hut")
        self.assertTrue(any(f.tag == GP.WALL for f in hut.faces))
        doors = [f for f in hut.faces if f.tag == G.palette_tag("wood", "oak") and min(p[1] for p in f.pts) < 0.01
                 and max(p[1] for p in f.pts) > 2.0]
        self.assertTrue(doors)
        self.assertGreater(min(p[2] for f in doors for p in f.pts), hut.meta["radius"] * 0.9)
        with self.assertRaises(ValueError):
            P.build("kit_grounds_hut", radius=6.0)

    def test_pine_crown_clears_a_walker(self):
        # Vegetation look A: the pine is a planting; its lowest needles (the kit's LOD0 sprays and
        # needle cone, at the planting's scale) stay over the clearance.
        for h in (GP.PINE_HEIGHT[0], 9.0, GP.PINE_HEIGHT[1]):
            p = P.build("kit_tree_pine", height=h, seed=2)
            self.assertEqual(p.triangles(), 0)
            (pl,) = p.meta["foliage"]
            g = FO.species_geo(pl.species, 0)
            leaves = [pl.point(q)[1] for q, uv in zip(g.pos, g.uv) if not FO.in_tile(uv, "bark")]
            self.assertGreaterEqual(min(leaves), GP.TREE_CROWN_CLEAR - 1e-9)
            self.assertAlmostEqual(pl.scale() * FO.species_top(pl.species), h, places=4)

    def test_glasshouse_seen_from_outside(self):
        p = P.build("kit_glasshouse_garden", scale=0.84)
        glass = G.palette_tag("glass", "glasshouse_glass")
        panes = [f for f in p.faces if f.tag == glass]
        self.assertGreater(len(panes), 0)
        sides: dict = {}
        for f in panes:  # every pane drawn both ways: the same corners, opposite normals
            key = frozenset((round(q[0], 5), round(q[1], 5), round(q[2], 5)) for q in f.pts)
            sides.setdefault(key, []).append(f.normal())
        for normals in sides.values():
            self.assertEqual(len(normals), 2)
            self.assertLess(sum(a * b for a, b in zip(*normals)), -0.99)
        self.assertIsNotNone(p.lod1)
        self.assertLess(sum(len(f.pts) - 2 for f in p.lod1), p.triangles() / 5)
        hx, hz = p.meta["half"]
        self.assertLessEqual(hx, (54.0 - 30.0) / 6.0 + 1e-6)  # three fit across the greenhouses' 24 m


class TerrainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gr = built()
        cls.g = cls.gr.g

    def test_lattice_stitched_from_the_cells(self):
        g = self.g
        self.assertEqual((g.nx, g.nz, g.step), (121, 101, 2.0))
        self.assertAlmostEqual(g.height(-112.0, 92.0), g.h[0][0])
        # sim-core's rule on the walkable rectangles' own heights is the lattice's.
        for x0, z0, x1, z1, hf in g.walk[:20]:
            for x, z in ((x0 + 0.7, z0 + 1.3), ((x0 + x1) / 2, (z0 + z1) / 2)):
                self.assertAlmostEqual(hf.height(x, z), g.height(x, z), places=9)

    def test_feet_on_the_ground_to_2_cm(self):
        for cell, cg in self.gr.cells.items():
            errs = GT.height_errors(self.g, cg.terrain, cell, G.P3A_HEIGHT_TOLERANCE)
            self.assertEqual(errs, [], cell)

    def test_the_check_catches_a_raised_vertex(self):
        cg = self.gr.cells["grounds_r1_c3"]
        m = GT.TerrainMesh(cg.terrain.name, list(cg.terrain.verts), list(cg.terrain.tris), skirt=set(cg.terrain.skirt))
        k = next(i for i, v in enumerate(m.verts) if self.g.walkable(v[0], v[2]) and i not in m.skirt)
        x, y, z = m.verts[k]
        m.verts[k] = (x, y + 0.05, z)
        self.assertTrue(GT.height_errors(self.g, m, "grounds_r1_c3", G.P3A_HEIGHT_TOLERANCE))

    def test_lod1_meets_lod0_and_neighbours_at_the_edges(self):
        for cell, cg in self.gr.cells.items():
            for side, pts in GT._edge_points(cg.terrain, self.g, cell).items():
                lod = GT._edge_points(cg.terrain_lod1, self.g, cell)[side]
                self.assertTrue(set(lod) <= set(pts), (cell, side))
            self.assertLess(cg.terrain_lod1.triangles(), cg.terrain.triangles())
        # A vertex shared by two cells has the same height in both.
        heights = {}
        for cg in self.gr.cells.values():
            for k, (x, y, z) in enumerate(cg.terrain.verts):
                if k in cg.terrain.skirt:
                    continue
                key = (round(x, 3), round(z, 3))
                if key in heights:
                    self.assertAlmostEqual(heights[key], y, places=3)
                heights[key] = y

    def test_triangles_face_up_and_skirts_hang_below(self):
        for cg in self.gr.cells.values():
            m = cg.terrain
            for t in m.tris:
                if any(k in m.skirt for k in t):
                    continue
                a, b, c = (m.verts[k] for k in t)
                ny = (b[2] - a[2]) * (c[0] - a[0]) - (b[0] - a[0]) * (c[2] - a[2])
                self.assertGreater(ny, 0.0)
            for k in m.skirt:
                x, y, z = m.verts[k]
                self.assertLess(y, self.g.height(x, z) - 1.0)

    def test_mask_and_shade(self):
        g = self.g
        pt = GT.Painter(g)
        path = g.meta["paths"]["hut"]["points"]
        x, z = (path[1][0] + path[2][0]) / 2, (path[1][1] + path[2][1]) / 2
        self.assertGreater(pt.mask(x, z)[0], 0.99)  # on the hut path
        self.assertLess(pt.mask(60.0, 205.0)[0], 0.01)  # the pitch terrace's turf
        self.assertGreater(pt.mask(-110.0, 100.0)[1], 0.99)  # the crag strip is rock
        self.assertGreater(pt.mask(5.0, 252.0)[0], 0.99)  # the beach by the Lake Shore
        for cg in self.gr.cells.values():
            for m in (cg.terrain, cg.terrain_lod1):
                self.assertTrue(all(GT.SHADE_MIN * 0.8 - 1e-9 <= s <= 1.0 for s in m.shade))
                self.assertTrue(all(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0 for u, v in m.uv1))
                self.assertEqual(len(m.uv0), len(m.verts))
                self.assertEqual(len(m.normals), len(m.verts))


class DressingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gr = built()
        cls.ctx = cls.gr.dctx

    def test_the_buildings_fill_their_holes(self):
        holes = {b["id"]: b for b in self.gr.g.meta["buildings"]}
        where = {"hut": "groundskeepers_cottage", "boathouse": "boathouse", "glasshouse_0": "greenhouses",
                 "glasshouse_1": "greenhouses", "glasshouse_2": "greenhouses"}
        placed = {p.id: p for p in self.gr.placed}
        for pid, hid in where.items():
            x, _, z = placed[pid].pos
            (x0, z0), (x1, z1) = holes[hid]["min"], holes[hid]["max"]
            self.assertTrue(x0 < x < x1 and z0 < z < z1, pid)
        # The hut's door faces west, onto the doorstep landmark.
        hut = placed["hut"]
        door = (hut.pos[0] + math.sin(hut.yaw) * 4.2, hut.pos[2] + math.cos(hut.yaw) * 4.2)
        self.assertLess(math.hypot(door[0] - 92.5, door[1] - 177.0), 2.6)

    def test_stone_circle_leaves_the_bridge_and_the_path_open(self):
        stones = [p for p in self.gr.placed if p.piece == "kit_standing_stone"]
        self.assertTrue(10 <= len(stones) <= 12, len(stones))
        for s in stones:
            x, _, z = s.pos
            self.assertFalse(98.0 <= x <= 110.0 and z <= 104.0, s.id)  # the bridge landing
            self.assertFalse(x < 98.0 and 109.0 < z < 115.0, s.id)  # the path from the gate

    def test_trees_and_rocks_keep_their_distances(self):
        ctx = self.ctx
        for i in self.gr.foliage.instances:
            x, y, z = i.pos
            self.assertTrue(D.free_spot(ctx, x, z, 0.0), i.id)
            if i.shape != "rock" and self.gr.g.walkable(x, z):
                self.assertGreaterEqual(D.CROWN_LOW[i.shape] * i.scale, GP.TREE_CROWN_CLEAR - 1e-9, i.id)
                self.assertIsNotNone(i.keep_out, i.id)
            if not self.gr.g.walkable(x, z):
                self.assertIsNone(i.keep_out, i.id)

    def test_keep_outs_are_current_few_and_clear_of_the_paths(self):
        with open(KEEP_OUT, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(D.keep_out_text(text, self.gr.keep_outs), text, "run tools/blender/world/grounds_dressing.py --write")
        rows = [k for k in json.loads(text)["keep_out"] if k["id"].startswith(D.KEEP_OUT_PREFIX)]
        self.assertEqual(len(rows), len(self.gr.keep_outs))
        self.assertLessEqual(len(rows), D.MAX_KEEP_OUTS)
        for k in rows:
            (x0, z0), (x1, z1) = k["min"], k["max"]
            for _, pts in self.ctx.paths:
                for a, b in zip(pts, pts[1:]):
                    for t in range(21):
                        px, pz = a[0] + (b[0] - a[0]) * t / 20, a[1] + (b[1] - a[1]) * t / 20
                        d = math.hypot(max(x0 - px, 0.0, px - x1), max(z0 - pz, 0.0, pz - z1))
                        self.assertGreaterEqual(d, 1.0, k["id"])

    def test_nothing_on_the_reserved_sites(self):
        for p in self.gr.placed:
            self.assertFalse(D._in_reserved(self.ctx, p.pos[0], p.pos[2], 0.0), p.id)
        for i in self.gr.foliage.instances:
            self.assertFalse(D._in_reserved(self.ctx, i.pos[0], i.pos[2], 0.0), i.id)


class LintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gr = built()
        cls.errs, cls.warns, cls.rep = GB.lint(cls.gr)

    def test_lint_clean(self):
        self.assertEqual(self.errs, [])

    def test_budgets(self):
        b = G.P3A_GROUNDS_BUDGET
        self.assertEqual(sorted(self.gr.cells), sorted(G.P3A_GROUNDS_CELLS))
        for cell, cg in self.gr.cells.items():
            self.assertLessEqual(cg.draws(), b.draws, cell)
            self.assertLessEqual(cg.tris(), b.tris[1], cell)
            self.assertLessEqual(cg.lod1_tris(), G.P3A_GROUNDS_LOD1_TRIS, cell)
        self.assertLessEqual(len(self.gr.foliage.shapes), G.P3A_FOLIAGE_DRAWS)
        self.assertLessEqual(self.gr.foliage.tris(), G.P3A_FOLIAGE_TRIS)
        self.assertLessEqual(sum(m.triangles() for m in self.gr.backdrop), G.P3A_BACKDROP_TRIS)
        self.assertLessEqual(len(self.gr.backdrop), G.P3A_BACKDROP_DRAWS)
        self.assertEqual(self.gr.lake.material, "lake_water")

    def test_the_lint_catches_a_breach(self):
        cg = self.gr.cells["grounds_r2_c5"]
        saved = cg.kit_materials
        cg.kit_materials = saved + ["a", "b", "c", "d"]
        try:
            errs, _, _ = GB.lint(self.gr)
        finally:
            cg.kit_materials = saved
        self.assertTrue(any("grounds_r2_c5" in e and "draws" in e for e in errs))

    def test_lod1_nodes_and_library(self):
        for cell, cg in self.gr.cells.items():
            for name, (md, base) in cg.lod_meshes.items():
                self.assertEqual(name, base + GB.LOD1_SUFFIX)
                self.assertIn(base, cg.meshes)
            self.assertIn(GB.TURF, cg.library())
            for md in cg.meshes.values():
                self.assertTrue(md.material in (GB.STONE, "castle_props", "castle_roof"), md.name)
        # The buildings' stone is the west range's pale stone outside (the one outside stone).
        self.assertEqual(GB.STONE, "castle_wall_c@ext")

    def test_greyscale_colour_0(self):
        for cg in self.gr.cells.values():
            for md in list(cg.meshes.values()) + [lm for lm, _ in cg.lod_meshes.values()]:
                self.assertTrue(GB._grey_ok(md.colors), md.name)
        for md in self.gr.foliage.shapes.values():
            self.assertTrue(GB._grey_ok(md.colors), md.name)
        for m in [self.gr.lake] + self.gr.backdrop:
            self.assertTrue(all(0.0 <= v <= 1.0 for v in m.grey), m.name)

    def test_deterministic(self):
        layout = L.load_layout(LAYOUT)
        with open(TEXTURES, encoding="utf-8") as f:
            textures = json.load(f)
        again = GB.build(layout, TERRAIN, textures)
        for cell in ("grounds_r0_c3", "grounds_r2_c5"):
            self.assertEqual(again.cells[cell].terrain.verts, self.gr.cells[cell].terrain.verts)
            for name, md in self.gr.cells[cell].meshes.items():
                self.assertEqual(again.cells[cell].meshes[name].verts, md.verts)
        self.assertEqual(again.lake.verts, self.gr.lake.verts)
        self.assertEqual([m.verts for m in again.backdrop], [m.verts for m in self.gr.backdrop])


class WaterAndBackdropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gr = built()

    def test_water_is_flat_and_never_under_the_castle(self):
        wy = self.gr.g.water_y
        hx0, hz0, hx1, hz1 = BD.CRAG_HOLE
        m = self.gr.lake
        self.assertTrue(all(abs(v[1] - wy) < 1e-6 for v in m.verts))
        for poly in m.polys:
            cx = sum(m.verts[k][0] for k in poly) / len(poly)
            cz = sum(m.verts[k][2] for k in poly) / len(poly)
            self.assertFalse(hx0 < cx < hx1 and hz0 < cz < hz1, (cx, cz))
        xs, zs = [v[0] for v in m.verts], [v[2] for v in m.verts]
        self.assertLessEqual(min(xs), -420.0)
        self.assertGreaterEqual(max(xs), 420.0)
        self.assertGreaterEqual(max(zs), 760.0)

    def test_depth_is_shallow_at_the_shore(self):
        m = self.gr.lake
        g = self.gr.g
        near = [m.grey[k] for k, v in enumerate(m.verts) if g.x0 < v[0] < g.x0 + 240 and g.z0 < v[2] < g.z0 + 200
                and abs(g.height(v[0], v[2]) - g.water_y) < 0.3]
        self.assertTrue(near)
        self.assertLess(max(near), 0.15)
        self.assertEqual(max(m.grey), 1.0)

    def test_backdrop_materials_and_palette_rows(self):
        with open(TEXTURES, encoding="utf-8") as f:
            pal = json.load(f)["palette"]
        for name in ("grounds_path", "grounds_rock", "lake_shallow", "lake_deep", "lake_band", "backdrop_forest",
                     "backdrop_rock"):
            self.assertRegex(pal[name], r"^#[0-9A-F]{6}$")
        self.assertEqual(sorted(m.material for m in self.gr.backdrop), ["backdrop_forest", "backdrop_rock"])
        with open(TOON, encoding="utf-8") as f:
            rows = json.load(f)["materials"]
        for name in ("grounds_terrain", "lake_water", "backdrop*"):
            self.assertIn(name, rows)

    def test_no_colour_literals_in_the_grounds_modules(self):
        world = os.path.join(os.path.dirname(HERE), "world")
        for name in sorted(os.listdir(world)):
            if name.startswith("grounds_") and name.endswith(".py"):
                with open(os.path.join(world, name), encoding="utf-8") as f:
                    self.assertEqual(G.colour_literals(f.read()), [], name)


if __name__ == "__main__":
    unittest.main()
