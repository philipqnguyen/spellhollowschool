"""The Owl Loft (N10), the
seventh-floor east corridor (N11) and the dungeon cellars (N12) as kitted
cells: lint clean within their budgets; the Owl Loft's tower over the layout's
solids with its door in their gap, only the loft's railings, its LOD1 and
camera shell, its owl markers and the owl prop; N11's plain south wall and
its tapestry; N12's plain passage-end wall, its steps, its furniture's
keep-outs; the HLOD chunk standing in for the Owl Loft. Pure Python but the
last class (reads the exported cells when they exist)."""

import json
import math
import os
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [os.path.dirname(HERE), os.path.join(os.path.dirname(HERE), "world")]

import hlod_build as HB  # noqa: E402
import layout_lib as L  # noqa: E402
import owl_prop as OP  # noqa: E402
from castle_kit import assemble as A  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import lint as LN  # noqa: E402
from castle_kit import pieces as P  # noqa: E402
from castle_kit import props_atlas as PA  # noqa: E402
from castle_kit.pieces import geom  # noqa: E402
from castle_kit.pieces import wings_b as WB  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
KEEP_OUT = os.path.join(ROOT, "data", "world", "keep_out.json")
EXTERIOR = os.path.join(ROOT, "data", "world", "exterior.json")
WORLD = os.path.join(ROOT, "client", "public", "assets", "world")
CELLS = ("owl_loft", "l7_corridor_east", "dungeon_cellars")
_B = {}


def builder() -> A.Builder:
    if "b" not in _B:
        _B["b"] = A.Builder.from_files()
    return _B["b"]


def build(cell: str) -> A.CellBuild:
    key = f"cb:{cell}"
    if key not in _B:
        _B[key] = builder().build(cell)
    return _B[key]


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class WingsBCellTests(unittest.TestCase):
    def test_the_three_cells_are_kitted_and_lint_clean_within_budget(self):
        self.assertEqual(G.P3C_HLOD_PENDING_B, ())
        for cell in CELLS:
            self.assertEqual(G.P3C_CELLS[cell], "wings_b")
            self.assertIn(cell, builder().kit_plan.cells, cell)
            cb = build(cell)
            rep = LN.lint_cell(cb, builder())
            self.assertEqual(rep.errors, [], cell)
            budget = G.BUDGETS[G.P3C_BUDGET[cell]]
            self.assertLessEqual(A.budget_draws(cb), budget.draws, cell)
            self.assertLessEqual(cb.triangles(), budget.tris[1], cell)

    def test_pieces_use_palette_slots_the_atlas_holds(self):
        _, atlas = PA.build()
        for name, params in WB.CATALOGUE.items():
            p = P.build(name, **params)
            for f in p.faces + (p.lod1 or []):
                ft = G.parse_face_tag(f.tag)
                if ft.mode != "kit":
                    continue
                tile, _, colour = ft.arg.partition(":")
                self.assertTrue(colour.startswith("@"), (name, f.tag))
                atlas.rect(tile, colour)  # raises for a slot the painted atlas lacks


class OwlLoftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cb = build("owl_loft")
        cls.layout = builder().layout
        cls.shell = next(u for u in cls.cb.uses if u.piece == A.OWL_LOFT_SHELL)
        cls.meta = cls.cb.pieces[cls.shell.id].meta

    def test_the_shell_stands_on_the_layout_walls_and_its_door_in_their_gap(self):
        cx, _, cz = self.shell.origin
        walls = [s for s in self.layout.solids if s[1][1] - s[0][1] > 20.0
                 and abs((s[0][0] + s[1][0]) / 2 - cx) < 10 and abs((s[0][2] + s[1][2]) / 2 - cz) < 10]
        self.assertEqual(len(walls), 5)  # owl_loft_wall_n, _s, _w, _e_n, _e_s
        lo = [min(s[0][k] for s in walls) for k in (0, 2)]
        hi = [max(s[1][k] for s in walls) for k in (0, 2)]
        h = self.meta["outside"]
        self.assertEqual([round(cx - h, 6), round(cz - h, 6)], lo)
        self.assertEqual([round(cx + h, 6), round(cz + h, 6)], hi)
        inner = [s for s in self.layout.surfaces if s.cell == "owl_loft" and s.kind != "door"]
        self.assertAlmostEqual(min(s.x0 for s in inner), cx - self.meta["inside"])
        self.assertAlmostEqual(max(s.z1 for s in inner), cz + self.meta["inside"])
        # The door: the east wall's gap between owl_loft_wall_e_n and owl_loft_wall_e_s.
        east = sorted((s for s in walls if s[0][0] >= cx + self.meta["inside"] - 1e-6), key=lambda s: s[0][2])
        gap = (east[0][1][2], east[1][0][2])
        door = next(t for t, w, sill, _ in WB.owl_loft_holes("e", self.meta["floor"], self.meta["loft"])
                    if abs(sill - self.meta["floor"]) < 1e-6)
        self.assertAlmostEqual(cz + door - WB.DOOR[0] / 2, gap[0])
        self.assertAlmostEqual(cz + door + WB.DOOR[0] / 2, gap[1])

    def test_only_the_loft_railings_over_the_stairwell_stay(self):
        rails = [u for u in self.cb.uses if u.role == "balustrade"]
        self.assertEqual(len(rails), 2)
        for u in rails:
            self.assertAlmostEqual(u.origin[1], self.meta["loft"])
        cx, _, cz = self.shell.origin
        for u in self.cb.uses:
            if u.role in ("balustrade", "newel"):
                self.assertLess(abs(u.origin[0] - cx), self.meta["inside"] - 1e-3, u.id)
                self.assertLess(abs(u.origin[2] - cz), self.meta["inside"] - 1e-3, u.id)

    def test_lod1_and_the_camera_shell(self):
        self.assertTrue(self.cb.lod_meshes)
        for md, base, dist in self.cb.lod_meshes.values():
            self.assertEqual(dist, G.FACADE_LOD, md.name)
        names = {base for _, base, _ in self.cb.lod_meshes.values()}
        self.assertIn("owl_loft.castle_wall_c@ext", names)
        self.assertIn("owl_loft.castle_roof", names)
        lod_ext = next(md for md, base, _ in self.cb.lod_meshes.values() if base == "owl_loft.castle_wall_c@ext")
        self.assertLess(lod_ext.triangles(), self.cb.meshes["owl_loft.castle_wall_c@ext"].triangles() / 10)
        # The camera's shell holds the tower's walls (the Planner draws none in this cell).
        xs = [p[0] for poly in self.cb.collider for p in poly]
        cx = self.shell.origin[0]
        reach = self.meta["outside"] + self.meta["eave_out"]  # the spire's eaves
        self.assertAlmostEqual(min(xs), cx - reach)
        self.assertAlmostEqual(max(xs), cx + reach)

    def test_owl_markers_are_empty_nodes_on_perches_and_sills(self):
        owls = A.owl_markers_p3c(self.cb)
        self.assertTrue(G.OWLS[0] <= len(owls) <= G.OWLS[1])
        loft = self.meta["loft"]
        phases = []
        for name, pos, yaw, phase in owls:
            self.assertRegex(name, r"^owl_loft\.owl_\d+$")
            md = self.cb.meshes[name]
            self.assertEqual((md.faces, md.verts), ([], []))
            self.assertEqual(md.origin, pos)
            anim = self.cb.extras[name][G.EXTRAS_ANIM]
            self.assertEqual((anim["kind"], anim["phase"], anim["yaw"]), ("owl", phase, yaw))
            self.assertTrue(0.0 <= phase < 1.0)
            self.assertGreater(pos[1], loft + 0.5)  # on the loft's perches and sills
            phases.append(phase)
        self.assertEqual(len(set(phases)), len(phases))
        self.assertEqual(A.budget_draws(self.cb), self.cb.draws())  # the markers draw nothing

    def test_the_owl_mail_stands_by_its_interactable_with_a_keep_out(self):
        post = next(u for u in self.cb.uses if u.piece == "kit_owl_mail")
        it = next(i for i in builder().layout.interactables if i.kind == "owl_mail")
        self.assertLess(math.hypot(post.origin[0] - it.pos[0], post.origin[2] - it.pos[2]), it.radius + 1.0)
        ko = {k["id"]: k for k in load(KEEP_OUT)["keep_out"]}["owl_loft_post"]
        self.assertLessEqual(ko["min"][0], post.origin[0] + 1e-6)
        self.assertFalse(ko["min"][0] <= it.pos[0] <= ko["max"][0] and ko["min"][1] <= it.pos[2] <= ko["max"][1])

    def test_the_hlod_chunk_stands_inside_the_tower(self):
        ext = load(EXTERIOR)
        chunk = next(c for c in ext["chunks"] if c["id"] == "hlod_owl_loft")
        self.assertEqual(chunk["cells"], ["owl_loft"])
        built = HB.build(builder().layout, ext, builder().kit_plan)["hlod_owl_loft"]
        self.assertEqual(sorted(built), sorted(HB.MATERIALS))
        cx, _, cz = self.shell.origin
        reach = self.meta["outside"] - G.P3A_HLOD_INSET + 1e-6
        for md in built.values():
            for x, y, z in md.verts:
                self.assertLessEqual(abs(x - cx), reach)
                self.assertLessEqual(abs(z - cz), reach)
                self.assertLess(y, self.meta["apex"])


class OwlPropTests(unittest.TestCase):
    def test_two_nodes_the_head_at_the_neck_within_its_triangles(self):
        owl = OP.build()
        self.assertEqual([p.name for p in owl.parts], ["owl_body", "owl_head"])
        self.assertEqual(owl.parts[1].origin, (0.0, OP.OWL_NECK, 0.0))
        self.assertLessEqual(owl.triangles(), G.OWL_TRIS)
        names = PA.palette()
        for zone in {f.zone for p in owl.parts for f in p.mesh.faces}:
            self.assertIn(OP.ZONES[zone], names)
        lo, hi = owl.parts[0].mesh.bounds()
        self.assertAlmostEqual(lo[1], 0.0, places=6)  # the body's origin at its feet
        self.assertLess(hi[1], OP.OWL_NECK + 0.08)  # (its shoulders)


class CorridorEastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cb = build("l7_corridor_east")

    def test_the_south_wall_is_left_plain(self):
        s = builder().layout.by_id("l7_corridor_east")
        for u in self.cb.uses:
            if u.role in ("plinth", "cornice", "floor_border", "threshold", "arch") or u.origin[1] > s.y + 6.0 \
                    or u.id == "hero:wishing_room_door":  # The Wishing Room's door, hidden until opened
                continue  # (the ribs' corbels under the cornice stay)
            self.assertGreater(s.z1 - u.origin[2], 1.0, u.id)  # nothing on or by the blank wall
        self.assertEqual([sh for sh in self.cb.plan.shafts if sh.run == "l7_corridor_east:s0"], [])

    def test_the_tapestry_hangs_opposite_the_blank_wall(self):
        lm = {m["id"]: m for m in builder().layout.landmarks}
        tap = next(u for u in self.cb.uses if u.piece == "kit_tapestry_weaver")
        self.assertAlmostEqual(tap.origin[0], lm["wishing_room_tapestry"]["pos"][0])
        self.assertAlmostEqual(tap.origin[0], lm["wishing_room_blank_wall"]["pos"][0])
        self.assertAlmostEqual(tap.origin[2], builder().layout.by_id("l7_corridor_east").z0)
        p = self.cb.pieces[tap.id]
        self.assertLessEqual(p.meta["proud"], G.TAPESTRY_PROUD + 1e-6)


class CellarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cb = build("dungeon_cellars")

    def test_the_passage_end_wall_is_left_plain(self):
        s = builder().layout.by_id("dungeon_cellars")
        for u in self.cb.uses:
            if u.role in ("plinth", "cornice", "floor_border"):
                continue
            near_wall = abs(u.origin[0] - s.x1) < 1.5
            self.assertFalse(near_wall and 9.5 < u.origin[2] < 14.5, u.id)

    def test_the_steps_are_solid_with_skirts_and_no_quoins(self):
        flights = [u for u in self.cb.uses if u.role == "flight"]
        self.assertEqual(len(flights), 2)
        for u in flights:
            self.assertTrue(u.kwargs()["solid"])
            self.assertIn(f"skirt:{u.run}", {x.id for x in self.cb.uses})
        self.assertFalse([u for u in self.cb.uses if u.id.startswith("quoin:")])
        for u in self.cb.uses:
            if u.role == "arch":
                self.assertFalse(u.kwargs()["quoins"])

    def test_furniture_has_keep_outs_clear_of_the_find_spots(self):
        kos = [k for k in load(KEEP_OUT)["keep_out"] if k["id"].startswith("cellar_")]
        furniture = [u for u in self.cb.uses if u.piece in ("kit_cellar_barrels", "kit_wine_rack", "kit_well_dry")]
        self.assertEqual(len(kos), len(furniture))
        for u in furniture:
            x, _, z = u.origin
            self.assertTrue(any(k["min"][0] - 1e-6 <= x <= k["max"][0] + 1e-6 and k["min"][1] - 1e-6 <= z <= k["max"][1] + 1e-6
                                for k in kos), u.id)
        for k in kos:
            self.assertEqual(k.get("cell"), "dungeon_cellars", k["id"])
        finds = [it for it in builder().layout.interactables if it.id.startswith("find_cellar")]
        self.assertEqual(len(finds), 2)
        for it in finds:
            for k in kos:
                self.assertFalse(k["min"][0] <= it.pos[0] <= k["max"][0] and k["min"][1] <= it.pos[2] <= k["max"][1],
                                 (it.id, k["id"]))


@unittest.skipUnless(os.path.exists(os.path.join(WORLD, "owl_loft.glb")), "no exported world")
class ExportTests(unittest.TestCase):
    def test_the_owl_markers_export_as_plain_nodes(self):
        with open(os.path.join(WORLD, "owl_loft.glb"), "rb") as f:
            data = f.read()
        (n,) = struct.unpack_from("<I", data, 12)
        doc = json.loads(data[20:20 + n])
        owls = [x for x in doc["nodes"] if x["name"].startswith("owl_loft.owl_")]
        if not owls:
            self.skipTest("the export predates the owl markers")
        self.assertEqual(len(owls), len(A.owl_markers_p3c(build("owl_loft"))))
        for x in owls:
            self.assertNotIn("mesh", x)
            self.assertEqual(x["extras"]["kit_anim"]["kind"], "owl")


if __name__ == "__main__":
    unittest.main()
