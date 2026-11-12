"""The Clock Tower
Courtyard and the covered bridge (N8), the east wing and the Hospital Wing
(N9), the Grand Staircase's moving flights (one swing node each, its B pose on
the layout's B surface), the landing gates' edges and hinge newels, the pier's
open corners, the landing rail prop, the palette rows, the keep-outs and the far
silhouette's chunk. Pure Python."""

import json
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [os.path.dirname(HERE), os.path.join(os.path.dirname(HERE), "world")]

import layout_lib as L  # noqa: E402
import stair_rail  # noqa: E402
from castle_kit import assemble as A  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import lint  # noqa: E402
from castle_kit import pieces as P  # noqa: E402
from castle_kit import props_atlas as PA  # noqa: E402
from castle_kit import rules as R  # noqa: E402
from castle_kit.pieces import geom, staircase, wings_a  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DATA = os.path.join(ROOT, "data")
CELLS = ("clock_courtyard", "covered_bridge", "tf_corridor_east", "hospital_wing")
GS = ("grand_staircase", "grand_staircase_upper")


def load(*parts):
    with open(os.path.join(DATA, *parts), encoding="utf-8") as f:
        return json.load(f)


_BUILDER = {}


def builder() -> A.Builder:
    if "b" not in _BUILDER:
        _BUILDER["b"] = A.Builder.from_files()
    return _BUILDER["b"]


_BUILDS = {}


def build(cell: str) -> A.CellBuild:
    if cell not in _BUILDS:
        _BUILDS[cell] = builder().build(cell)
    return _BUILDS[cell]


class WingsAPieceTests(unittest.TestCase):
    CAPS = {"kit_bed_ward": wings_a.BED_TRIS, "kit_screen_ward": wings_a.SCREEN_TRIS,
            "kit_bridge_covered": wings_a.BRIDGE_TRIS}

    def test_every_piece_builds_within_its_cap_on_palette_colours(self):
        palette = PA.palette()
        slots = {tuple(s) for s in load("art", "castle_textures.json")["props_slots"]}
        for name, params in wings_a.CATALOGUE.items():
            p = P.build(name, **params)
            self.assertGreater(p.triangles(), 0, name)
            if name in self.CAPS:
                self.assertLessEqual(p.triangles(), self.CAPS[name], name)
            for f in p.faces + (p.lod1 or []):
                self.assertNotRegex(f.tag, r"#[0-9A-Fa-f]{6}", name)
                if f.tag.startswith("castle_props|kit:") and f.tag.count(":") == 2:
                    tile, colour = f.tag.split("|kit:")[1].split(":")
                    self.assertIn(colour[1:], palette, (name, f.tag))
                    self.assertIn((tile, colour), slots, (name, f.tag))

    def test_the_wings_a_palette_rows_and_slots_are_appended(self):
        tex = load("art", "castle_textures.json")
        names = list(tex["palette"])
        block = names[names.index("_doc_p3c_wings_a") + 1:]
        for n in ("gs_flight_stone", "gs_flight_under", "gs_flight_trim", "gs_flight_marble", "ward_blanket", "shingle"):
            self.assertIn(n, block)
        for tile, colour in wings_a.SLOTS:
            self.assertIn([tile, colour], tex["props_slots"])

    def test_the_locked_door_is_an_opening_piece(self):
        spec = G.OPENING_PIECES["kit_door_locked_3x4.5"]
        self.assertEqual((spec.kind, spec.width, spec.height), ("blind_door", 3.0, 4.5))

    def test_the_landing_rail(self):
        md = stair_rail.rail_mesh()
        self.assertLessEqual(md.triangles(), G.RAIL_TRIS)
        self.assertEqual(md.material, "castle_trim")
        lo = [min(v[k] for v in md.verts) for k in range(3)]
        hi = [max(v[k] for v in md.verts) for k in range(3)]
        # The staircase's balustrade between the hinges' newels: from the first hinge along +x,
        # standing on y = 0, its rail centred on the gate line (the coping overhangs -z).
        self.assertAlmostEqual(lo[0], R.NEWEL_HALF, places=6)
        self.assertAlmostEqual(hi[0], G.GATE_WIDTH - R.NEWEL_HALF, places=6)
        self.assertAlmostEqual(lo[1], 0.0, places=6)
        self.assertAlmostEqual(hi[1], 1.0, places=6)
        self.assertAlmostEqual(hi[2], stair_rail.RAIL / 2, places=6)
        self.assertLess(abs(lo[2]), stair_rail.RAIL)
        # Every corner has a trim UV and a grey shade.
        self.assertEqual(len(md.uvs), len(md.faces))
        self.assertTrue(all(0.5 <= c[0] <= 1.0 and c[0] == c[1] == c[2] for f in md.colors for c in f))
        # Every gate is GATE_WIDTH between its hinges.
        for f in load("world", "stairs.json")["flights"]:
            for g in f["gates"]:
                (xa, za), (xb, zb) = g["line"]
                self.assertAlmostEqual(math.hypot(xb - xa, zb - za), G.GATE_WIDTH, places=6, msg=g["id"])


class WingsAStaircaseTests(unittest.TestCase):
    def test_one_swing_node_per_moving_flight(self):
        stairs = load("world", "stairs.json")
        cb = build("grand_staircase_upper")
        for f in stairs["flights"]:
            name = G.SWING_NODE.format(cell="grand_staircase_upper", flight=f["id"])
            self.assertIn(name, cb.meshes)
            md = cb.meshes[name]
            self.assertEqual(md.material, "castle_props")
            anim = cb.extras[name][G.EXTRAS_ANIM]
            self.assertEqual(anim["kind"], G.ANIM_SWING)
            self.assertEqual(anim["flight"], f["id"])
            self.assertEqual(tuple(anim["pivot"]), tuple(float(v) for v in f["pivot"]))
            self.assertAlmostEqual(anim["angle"], math.radians(f["angle_deg"]), places=5)
            a = builder().layout.by_id(f["a"])
            self.assertEqual(md.origin, (float(f["pivot"][0]), float(min(a.y, a.y_top)), float(f["pivot"][1])))
            self.assertNotIn(G.lod1_name(name), cb.lod_meshes)
            # Its B pose: the treads turned about the pivot lie on the layout's B surface.
            err = lint.swing_b_error(cb, builder().layout, f)
            self.assertIsNotNone(err)
            self.assertLessEqual(err, 0.02, f["id"])
            # The balustrades move with it: both sides' rails are in the node.
            srcs = {sf.src for sf in md.src_faces}
            self.assertEqual(sum(1 for s in srcs if s.startswith(f"balustrade:{f['a']}:")), 2, f["id"])

    def test_the_grand_staircase_draws_within_its_budget(self):
        draws = sum(A.budget_draws(build(c)) for c in GS)
        self.assertLessEqual(draws, G.BUDGETS["grand_staircase"].draws)
        self.assertEqual(G.BUDGETS["grand_staircase"].draws, 22 + G.P3C_GS_DRAWS_ADD)

    def test_gate_edges_carry_no_balustrade_and_hinges_carry_newels(self):
        stairs = load("world", "stairs.json")
        uses = {c: build(c).uses for c in GS}
        lay = builder().layout
        for f in stairs["flights"]:
            for g in f["gates"]:
                s = lay.by_id(g["landing"])
                cell_uses = uses[s.cell]
                (xa, za), (xb, zb) = g["line"]
                mid = ((xa + xb) / 2, (za + zb) / 2)
                for u in cell_uses:
                    if u.role != "gallery_edge" or u.run != s.id:
                        continue
                    kw = u.kwargs()
                    p = build(s.cell).pieces[u.id]
                    has_rail = any("balustrade" in fc.tag or fc.tag.endswith("trim:rail") for fc in p.faces)
                    if not has_rail:
                        continue
                    # A railing segment's plan extent must not reach the gate's middle.
                    pl = geom.Placement(u.origin, u.yaw)
                    a0, a1 = kw["inset"][0], kw["length"] - kw["inset"][1]
                    e0, e1 = pl.point((a0, 0.0, 0.0)), pl.point((a1, 0.0, 0.0))
                    lo_x, hi_x = sorted((e0[0], e1[0]))
                    lo_z, hi_z = sorted((e0[2], e1[2]))
                    inside = lo_x - 1e-6 <= mid[0] <= hi_x + 1e-6 and lo_z - 1e-6 <= mid[1] <= hi_z + 1e-6
                    self.assertFalse(inside, (g["id"], u.id))
                for hx, hz in g["line"]:
                    near = [u for u in cell_uses if u.role == "newel" and math.hypot(u.origin[0] - hx, u.origin[2] - hz)
                            < R.NEWEL_NEAR + 1e-6 and abs(u.origin[1] - s.y) < R.NEWEL_DY]
                    self.assertTrue(near, (g["id"], hx, hz))

    def test_no_two_newels_share_a_spot(self):
        for c in GS:
            newels = [u for u in build(c).uses if u.role == "newel"]
            for i, a in enumerate(newels):
                for b in newels[i + 1:]:
                    same = math.hypot(a.origin[0] - b.origin[0], a.origin[2] - b.origin[2]) < 0.35 \
                        and abs(a.origin[1] - b.origin[1]) < R.NEWEL_DY
                    self.assertFalse(same, (a.id, b.id))

    def test_the_pier_opens_the_swept_corners(self):
        """The open colonnettes are exactly those beside a half-flight's sweep round the pier."""
        stairs = load("world", "stairs.json")
        kit = load("world", "castle_kit.json")["cells"]["grand_staircase"]
        pier = next(h for h in kit["hero"] if h["id"] == "pier")
        cx, cz = pier["pos"][0], pier["pos"][2]
        size = P.parse(pier["piece"])[1]
        want = set()
        lands = staircase.pier_landings(pier["args"]["top"])
        lay = L.load_layout(os.path.join(DATA, "world", "layout.json"), moving=True)
        for f in stairs["flights"]:
            if (f["pivot"][0], f["pivot"][1]) != (cx, cz):
                continue
            a, b = lay.by_id(f["a"]), lay.by_id(f["b"])
            da = ((a.x0 + a.x1) / 2 - cx, (a.z0 + a.z1) / 2 - cz)
            db = ((b.x0 + b.x1) / 2 - cx, (b.z0 + b.z1) / 2 - cz)
            sx, sz = math.copysign(1.0, da[0] + db[0]), math.copysign(1.0, da[1] + db[1])
            y0, y1 = min(a.y, a.y_top) - 1.2, max(a.y, a.y_top) + 1.2
            for lo, hi in zip(lands, lands[1:]):
                if lo < y1 and hi - staircase.SLAB > y0:
                    want.add((sx, sz, lo))
        got = set(wings_a.open_corners(size, pier["args"]["top"], pier["args"]["open"]))
        self.assertEqual(got, want)
        p = P.build(pier["piece"], **pier["args"])
        kept = {(math.copysign(1.0, x), math.copysign(1.0, z), y0) for x, z, y0, _ in p.meta["colonnettes"]}
        self.assertFalse(kept & want)
        self.assertEqual(len(kept) + len(want), 4 * (len(lands) - 1))

    def test_landings_the_flights_reach_at_b_are_carried(self):
        heroes = {h["id"]: h for c in GS for h in load("world", "castle_kit.json")["cells"][c]["hero"]}
        for hid in ("balcony_l3", "balcony_l7", "corbel_l6"):
            self.assertTrue(heroes[hid]["piece"].startswith("kit_landing_corbel_"), hid)


class WingsACellTests(unittest.TestCase):
    def test_the_cells_are_built_and_lint_clean(self):
        kit = load("world", "castle_kit.json")["cells"]
        for c in CELLS + GS:
            self.assertTrue(kit[c]["build"], c)
            rep = lint.lint_cell(build(c), builder())
            self.assertEqual(rep.errors, [], c)

    def test_budgets(self):
        for c in CELLS:
            cb = build(c)
            cap = G.draw_cap(c, cb.plan.budget)
            self.assertLessEqual(A.budget_draws(cb), cap, c)
            self.assertLessEqual(cb.triangles(), G.BUDGETS[cb.plan.budget].tris[1], c)
        self.assertEqual(G.P3C_BUDGET["covered_bridge"], "bridge")

    def test_the_clock_courtyard_south_wall_is_a_parapet(self):
        cb = build("clock_courtyard")
        x0, x1 = A.CLOCK_INNER_X
        z0, z1 = A.CLOCK_SOUTH
        for f in cb.faces:
            if f.src != "base" or not all(z0 - 0.01 <= p[2] <= z1 + 0.01 for p in f.pts):
                continue
            xs = [p[0] for p in f.pts]
            if min(xs) >= x1 - 0.01 or max(xs) <= x0 + 0.01:
                continue
            if all(abs(p[0] - x0) < 1e-6 for p in f.pts) or all(abs(p[0] - x1) < 1e-6 for p in f.pts):
                continue  # the corner blocks' faces toward the parapet
            self.assertLessEqual(max(p[1] for p in f.pts), R.CLOCK_PARAPET + 1e-6, f.pts)
        self.assertTrue(any(u.id.startswith("coping:") for u in cb.uses))

    def test_the_clock_face_hangs_on_the_clock_tower(self):
        ext = load("world", "exterior.json")
        tower = next(m for m in ext["masses"] if m["id"] == "clock_tower")
        hero = next(h for h in load("world", "castle_kit.json")["cells"]["clock_courtyard"]["hero"] if h["id"] == "clock_face")
        d = P.parse(hero["piece"])[1]
        self.assertGreaterEqual(hero["pos"][0] - d / 2, tower["min"][0])
        self.assertLessEqual(hero["pos"][0] + d / 2, tower["max"][0])
        self.assertAlmostEqual(hero["pos"][2], tower["max"][1])
        self.assertGreater(hero["pos"][1], 16.0)  # over the courtyard's 16 m walls
        self.assertAlmostEqual(hero["pos"][1] + d / 2, 30.0, delta=1.0)

    def test_the_bridge_deck_is_the_layout_surface(self):
        lay = builder().layout
        s = lay.by_id("covered_bridge")
        cb = build("covered_bridge")
        hero = next(u for u in cb.uses if u.role == "hero")
        p = cb.pieces[hero.id]
        pl = geom.Placement(hero.origin, hero.yaw)
        deck = [[pl.point(q) for q in f.pts] for f in p.faces if f.normal()[1] > 0.99]
        top = max(deck, key=lambda pts: geom.norm(geom.newell(pts)))  # the deck: the largest upward face
        for x, y, z in top:
            self.assertAlmostEqual(y, s.height(min(max(x, s.x0), s.x1), min(max(z, s.z0), s.z1)), delta=0.01)
        xs, zs = [q[0] for q in top], [q[2] for q in top]
        self.assertEqual((round(min(xs), 3), round(max(xs), 3), round(min(zs), 3), round(max(zs), 3)),
                         (s.x0, s.x1, s.z0, s.z1))
        self.assertFalse(any(f.src == "base" for f in cb.faces))

    def test_the_locked_door_stands_at_its_landmark(self):
        cb = build("tf_corridor_east")
        door = next(u for u in cb.uses if u.piece == "kit_door_locked_3x4.5")
        mark = next(m for m in builder().layout.landmarks if m["id"] == "locked_door")
        self.assertAlmostEqual(door.origin[2], mark["pos"][2], delta=0.01)
        self.assertLess(abs(door.origin[0] - mark["pos"][0]), 2.0)


class WingsADataTests(unittest.TestCase):
    def test_hlod_chunk_for_n8(self):
        self.assertEqual(G.P3C_HLOD_PENDING_A, ())
        self.assertEqual(G.P3C_HLOD_CHUNKS_A, 1)
        ext = load("world", "exterior.json")
        chunk = next(c for c in ext["chunks"] if c["id"] == "hlod_clock_court")
        self.assertEqual(sorted(chunk["cells"]), ["clock_courtyard", "covered_bridge"])

    def test_keep_outs_match_the_furniture(self):
        rows = {k["id"]: k for k in load("world", "keep_out.json")["keep_out"]}
        for cell, prefix, want in (("hospital_wing", "hospital_", ("bed_", "screen_")),
                                   ("clock_courtyard", "clock_", ("lamp_", "well", "bench_", "planter_"))):
            cp = builder().kit_plan.cells[cell]
            for h in cp.hero:
                if not h.id.startswith(want):
                    continue
                row = rows[prefix + h.id]
                self.assertEqual(row["cell"], cell)
                piece = P.build(h.piece, **dict(h.args))
                pl = geom.Placement(h.pos, h.yaw)
                pts = [pl.point(p) for f in piece.faces for p in f.pts]
                pts = [p for p in pts if p[1] - h.pos[1] < 2.0]
                self.assertAlmostEqual(row["min"][0], min(p[0] for p in pts), delta=0.011, msg=h.id)
                self.assertAlmostEqual(row["max"][1], max(p[2] for p in pts), delta=0.011, msg=h.id)
        # No keep-out covers a key place's landmark.
        marks = {m["id"]: m["pos"] for m in builder().layout.landmarks}
        for kp in ("hospital_wing", "clock_courtyard"):
            m = marks[kp]
            for r in rows.values():
                if r["id"].startswith(("hospital_", "clock_")):
                    inside = r["min"][0] <= m[0] <= r["max"][0] and r["min"][1] <= m[2] <= r["max"][1]
                    self.assertFalse(inside, (kp, r["id"]))


if __name__ == "__main__":
    unittest.main()
