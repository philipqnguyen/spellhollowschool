"""Grounds edges (docs/architecture.md):
the playtest's two gaps where the grounds meet the castle.

* The hill: the crag strip west of the walkable grounds stood 10 m tall at the
  box's north edge and ended there in a sheer cut short of the great west tower,
  the lake showing through the slot under it (the terrain's skirts face out, so
  from the grounds they are not drawn). The crag now eases down to the ground
  toward the castle side, and the backdrop's rock follows the terrain's edge at
  every lattice point: `grounds_backdrop.edge_gaps` (also in the grounds lint).
* The wall foot: the courtyard's south wall's outer face (the Planner's) stopped
  at the highest ground of the grounds rectangle beyond it (0.58 m), so it hung
  over the grass, the courtyard's paving showing under it. Grounds are no room
  sharing the wall's mass; the face runs down past the lowest ground near it:
  `grounds_terrain.foot_errors` over every cell's outer faces and the far
  silhouette's stand-ins.
"""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [os.path.dirname(HERE), os.path.join(os.path.dirname(HERE), "world")]

import grounds_backdrop as BD  # noqa: E402
import grounds_terrain as GT  # noqa: E402
import hlod_build as HB  # noqa: E402
import layout_lib as L  # noqa: E402
import terrain_layout as TL  # noqa: E402
from castle_kit import schema as S  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
LAYOUT = os.path.join(ROOT, "data", "world", "layout.json")
TERRAIN = os.path.join(ROOT, "data", "world", "terrain")
EXTERIOR = os.path.join(ROOT, "data", "world", "exterior.json")

_C: dict = {}


def layout():
    if "layout" not in _C:
        _C["layout"] = L.load_layout(LAYOUT)
        _C["g"] = GT.Grounds.load(TERRAIN, _C["layout"])
    return _C["layout"]


def grounds() -> GT.Grounds:
    layout()
    return _C["g"]


def cellars() -> list:
    with open(EXTERIOR, encoding="utf-8") as f:
        cells = set(json.load(f).get("no_outside", []))
    return [(s.x0 - 2.0, s.z0 - 2.0, s.x1 + 2.0, s.z1 + 2.0) for s in layout().surfaces if s.cell in cells]


class HillTests(unittest.TestCase):
    def test_no_sheer_drop_round_the_terrain(self):
        bd = BD.Backdrop(grounds(), cellars())
        self.assertEqual(BD.edge_gaps(bd, BD.rock_mesh(bd)), [])

    def test_the_check_finds_a_sheer_drop(self):
        # The backdrop's box-edge rock 2 m under the terrain instead of 0.3: a cut all round.
        saved = BD.EDGE_DROP
        BD.EDGE_DROP = 2.0
        try:
            bd = BD.Backdrop(grounds(), cellars())
            errs = BD.edge_gaps(bd, BD.rock_mesh(bd))
        finally:
            BD.EDGE_DROP = saved
        self.assertTrue(any("north edge" in e for e in errs))
        self.assertTrue(any("west edge" in e for e in errs))

    def test_the_backdrop_follows_the_terrain_edge(self):
        # Along the box's west edge (the crag strip) every lattice point has a rock vertex
        # EDGE_DROP under the terrain.
        g = grounds()
        bd = BD.Backdrop(g, cellars())
        rock = BD.rock_mesh(bd)
        verts = {(round(v[0], 3), round(v[2], 3)): v[1] for v in rock.verts}
        for z in range(92, 160, 2):
            y = verts.get((g.x0, float(z)))
            self.assertIsNotNone(y, z)
            self.assertAlmostEqual(y, g.height(g.x0, z) - BD.EDGE_DROP, places=3)

    def test_the_crag_eases_down_to_the_castle_side(self):
        g = grounds()
        z0 = g.z0
        beside = g.height(TL.WEST_X, z0)  # the walkable ground's edge
        for x in range(int(g.x0), int(TL.WEST_X) + 1, 2):
            self.assertLess(abs(g.height(x, z0) - beside), 1.5, x)
        # ... and still rises west of the walkable ground further south.
        self.assertGreater(g.height(g.x0, z0 + 20.0), g.height(TL.WEST_X, z0 + 20.0) + 4.0)


class WallFootTests(unittest.TestCase):
    def test_the_outer_faces_meet_the_ground(self):
        plan = L.plan_layout(layout())
        errs = []
        for (cell, mat), polys in sorted(plan.polys.items()):
            if mat == "wall_exterior":
                errs += GT.foot_errors(grounds(), cell, polys)
        self.assertEqual(errs, [])

    def test_the_courtyard_south_wall_reaches_under_the_grass(self):
        g = grounds()
        plan = L.plan_layout(layout())
        faces = [p for p in plan.polys[("courtyard", "wall_exterior")]
                 if all(abs(q[2] - 91.0) < 1e-6 for q in p) and min(q[1] for q in p) < 2.0]  # not the gate's lintel
        self.assertTrue(faces)
        spans = []
        for p in faces:
            xs = [q[0] for q in p]
            y0 = min(q[1] for q in p)
            spans.append((min(xs), max(xs)))
            for x in (min(xs) + 0.1, (min(xs) + max(xs)) / 2, max(xs) - 0.1):
                self.assertLess(y0, g.drawn_height(x, 91.05) - 0.1, (x, y0))
        # Both sides of the gate (x 12..20), from the west corner to the east one.
        self.assertLessEqual(min(a for a, _ in spans), -19.0)
        self.assertGreaterEqual(max(b for _, b in spans), 51.0)

    def test_the_stand_ins_meet_the_ground(self):
        with open(EXTERIOR, encoding="utf-8") as f:
            ext = json.load(f)
        plan, _ = S.resolve(S.load_json(S.KIT_PATH), layout(), S.PROPS_PATH, S.load_texture_set())
        errs = []
        for chunk, mats in HB.build(layout(), ext, plan).items():
            polys = [[md.verts[i] for i in fc] for md in mats.values() for fc in md.faces]
            errs += GT.foot_errors(grounds(), chunk, polys)
        self.assertEqual(errs, [])

    def test_the_check_finds_a_hanging_foot(self):
        g = grounds()
        h = g.drawn_height(30.0, 91.05)
        # A face at z 91 facing the grounds (+z), its foot 0.4 m over the lip.
        face = [(28.0, h + 0.4, 91.0), (32.0, h + 0.4, 91.0), (32.0, h + 5.0, 91.0), (28.0, h + 5.0, 91.0)]
        self.assertTrue(GT.foot_errors(g, "test", [face]))
        down = [(x, (h - 0.5) if y < h + 1 else y, z) for x, y, z in face]
        self.assertEqual(GT.foot_errors(g, "test", [down]), [])


if __name__ == "__main__":
    unittest.main()
