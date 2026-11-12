"""Secrets (docs/art/castle-kit.md; docs/architecture.md "Secrets and the map"):
the five hidden passages kitted in the `passage` kind, lint clean within
its budget; their walk-through entrances as one moving castle_props node each
in the public rooms (grid.ENTRANCE_NODE, kit_anim entrance) over plain arches;
the Wishing Room's door node; the passages' sloping ceilings; the two
Wishing Rooms through the instance-room kit adapter with the contract's
ids; the 3c leftovers (the ravine under the covered bridge, the swing nodes'
trimmed corners). Pure Python."""

import json
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [os.path.dirname(HERE), os.path.join(os.path.dirname(HERE), "world")]

import instance_kit as IK  # noqa: E402
import layout_lib as L  # noqa: E402
import rooms_wishing_room as RR  # noqa: E402
from castle_kit import assemble as A  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import lint as LN  # noqa: E402
from castle_kit import pieces as P  # noqa: E402
from castle_kit import props_atlas as PA  # noqa: E402
from castle_kit import rules as R  # noqa: E402
from castle_kit.pieces import geom  # noqa: E402
from castle_kit.pieces import secrets as SP  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
EXTERIOR = os.path.join(ROOT, "data", "world", "exterior.json")
SECRETS = os.path.join(ROOT, "data", "world", "secrets.json")
INSTANCES = os.path.join(ROOT, "data", "world", "instances")
PASSAGES = tuple(sorted(G.P3D_CELLS))
# The public side of every passage door: (door, cell, style).
ENTRANCES = (
    ("sp_tapestry_door_l5", "l5_corridor_west", "tapestry"),
    ("sp_tapestry_door_l4", "l4_corridor_west", "tapestry"),
    ("sp_panel_door_trophy", "trophy_room", "panel"),
    ("sp_panel_door_corridor", "ff_corridor_north", "panel"),
    ("sp_painting_door_library", "sf_library", "painting"),
    ("sp_painting_door_charms", "tf_corridor_west", "painting"),
    ("sp_statue_door_gallery", "ff_gallery_east", "statue"),
    ("sp_statue_door_undercroft", "gf_undercroft", "wall"),
    ("sp_dungeon_door_corridor", "dungeon_west", "wall"),
    ("sp_dungeon_door_cellar", "dungeon_cellars", "wall"),
)
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


def room(lid: str):
    key = f"room:{lid}"
    if key not in _B:
        r = RR.KIT_ROOMS[lid]()
        _B[key] = (r, IK.build(r))
    return _B[key]


class PassageTests(unittest.TestCase):
    def test_the_five_passages_are_kitted_lint_clean_within_the_passage_budget(self):
        self.assertEqual(len(PASSAGES), 5)
        budget = G.BUDGETS["passage"]
        for cell in PASSAGES:
            self.assertIn(cell, builder().kit_plan.cells, cell)
            cb = build(cell)
            rep = LN.lint_cell(cb, builder())
            self.assertEqual(rep.errors, [], cell)
            self.assertLessEqual(A.budget_draws(cb), budget.draws, cell)
            self.assertGreaterEqual(cb.triangles(), budget.tris[0], cell)
            self.assertLessEqual(cb.triangles(), budget.tris[1], cell)

    def test_passages_draw_rough_stone_no_arch_quoin_or_moulding(self):
        doors = R.secret_doors(builder().layout)
        for cell in PASSAGES:
            cb = build(cell)
            for u in cb.uses:
                self.assertNotIn(u.role, ("arch", "quoin", "cornice", "plinth"), (cell, u.id))
            self.assertEqual({m.material for m in cb.meshes.values()} - {"castle_props", "castle_trim", "castle_ceiling"},
                             {"castle_wall_d", "castle_floor_dungeon"}, cell)
            self.assertTrue(any(op.door in doors for op in cb.plan.openings), cell)

    def test_every_stair_has_its_low_ceiling_3_5_m_over_the_ramp_and_in_the_collider(self):
        layout = builder().layout
        for cell in PASSAGES:
            cb = build(cell)
            ceilings = [u for u in cb.uses if u.piece == "kit_passage_ceiling"]
            stairs = [s for s in layout.surfaces if s.cell == cell and s.sloped]
            self.assertEqual(len(ceilings), len(stairs), cell)
            for s in stairs:
                u = next(u for u in ceilings if s.contains(u.origin[0], u.origin[2], 0.01))
                quad = cb.pieces[u.id].meta["collider"][0]
                pl = geom.Placement(u.origin, u.yaw)
                for p in (pl.point(q) for q in quad):
                    self.assertAlmostEqual(p[1] - s.height(min(max(p[0], s.x0), s.x1), min(max(p[2], s.z0), s.z1)),
                                           SP.PASSAGE_CLEAR, delta=0.02, msg=(cell, u.id))
                self.assertTrue(any(all(abs(a[k] - b[k]) < 1e-6 for k in range(3)) for poly in cb.collider
                                    for a, b in zip(poly, [pl.point(q) for q in quad])), (cell, u.id))

    def test_the_passages_join_their_neighbours_chunks(self):
        self.assertEqual(G.P3D_HLOD_PENDING, ())
        with open(EXTERIOR, encoding="utf-8") as f:
            ext = json.load(f)
        chunk = {c: ch["id"] for ch in ext["chunks"] for c in ch["cells"]}
        self.assertEqual(chunk["passage_tapestry"], "hlod_west_wing")
        self.assertEqual(chunk["passage_painting"], "hlod_west_wing")
        self.assertEqual(chunk["passage_panel"], "hlod_court")
        self.assertEqual(chunk["passage_statue"], "hlod_great_hall")
        self.assertIn("passage_dungeon", ext["no_outside"])

    def test_the_export_is_deterministic(self):
        b2 = A.Builder.from_files()
        for cell in ("passage_panel", "trophy_room"):
            self.assertEqual(b2.build(cell).digest(), build(cell).digest(), cell)


class EntranceTests(unittest.TestCase):
    def test_every_passage_door_has_one_entrance_node_on_its_public_side(self):
        layout = builder().layout
        with open(SECRETS, encoding="utf-8") as f:
            sec = json.load(f)
        doors = {d for s in sec["secrets"] for d in s["doors"]}
        self.assertEqual(doors, {d for d, _, _ in ENTRANCES})
        for door, cell, style in ENTRANCES:
            cb = build(cell)
            rep = LN.lint_cell(cb, builder())
            self.assertEqual(rep.errors, [], cell)
            self.assertLessEqual(A.budget_draws(cb), G.draw_cap(cell, cb.plan.budget), cell)
            name = G.ENTRANCE_NODE.format(cell=cell, door=door)
            md = cb.meshes[name]
            self.assertTrue(md.faces and all(f.walkthrough for f in md.src_faces), name)
            anim = cb.extras[name][G.EXTRAS_ANIM]
            self.assertEqual((anim["kind"], anim["door"], anim["style"]), (G.ANIM_ENTRANCE, door, style), name)
            # The origin: the door's middle on the room's wall plane, at its floor.
            d = layout.by_id(door)
            op = next(o for o in cb.plan.openings if o.kind == "arch" and o.door == door)
            want = (op.c, d.y, op.centre) if op.axis == "x" else (op.centre, d.y, op.c)
            self.assertEqual(tuple(round(v, 4) for v in md.origin), tuple(round(v, 4) for v in want), name)
            # Facing into the room.
            yaw = R.yaw_for(op.axis, op.sign)
            self.assertAlmostEqual(math.cos(anim["yaw"] - yaw), 1.0, places=3, msg=name)
            # Its arch is plain: no rim, no quoins.
            arch = next(u for u in cb.uses if u.role == "arch" and u.id.endswith(f":arch:{door}"))
            self.assertEqual((arch.kwargs()["rim"], arch.kwargs()["quoins"]), (False, False), name)

    def test_the_entrances_cover_the_opening(self):
        for door, cell, style in ENTRANCES:
            md = build(cell).meshes[G.ENTRANCE_NODE.format(cell=cell, door=door)]
            o = md.origin
            pts = [(v[0] - o[0], v[1] - o[1], v[2] - o[2]) for v in md.verts]
            half = max(max(abs(p[0]), abs(p[2])) for p in pts)  # along the wall (the depth is far less)
            self.assertGreaterEqual(half, SP.DOOR_W / 2 - 1e-6, door)
            self.assertGreaterEqual(max(p[1] for p in pts), SP.DOOR_H - 0.05, door)

    def test_the_wishing_room_door_node(self):
        cb = build("l7_corridor_east")
        md = cb.meshes[G.WISHING_ROOM_DOOR_NODE]
        self.assertTrue(md.faces)
        self.assertEqual(cb.extras[G.WISHING_ROOM_DOOR_NODE][G.EXTRAS_ANIM]["kind"], G.ANIM_WISHING_ROOM_DOOR)
        it = next(i for i in builder().layout.interactables if i.id == "wishing_room_door")
        self.assertAlmostEqual(md.origin[0], it.pos[0])
        self.assertAlmostEqual(md.origin[1], it.pos[1])
        self.assertAlmostEqual(md.origin[2], 9.5)  # the blank south wall's face
        self.assertLessEqual(max(v[2] for v in md.verts) - min(v[2] for v in md.verts), SP.RR_PROUD + 1e-3)
        self.assertEqual(LN.lint_cell(cb, builder()).errors, [])

    def test_pieces_use_palette_slots_the_atlas_holds(self):
        _, atlas = PA.build()
        for name, params in SP.CATALOGUE.items():
            p = P.build(name, **params)
            for f in p.faces + (p.lod1 or []):
                ft = G.parse_face_tag(f.tag)
                if ft.mode != "kit":
                    continue
                tile, _, colour = ft.arg.partition(":")
                if colour:
                    atlas.rect(tile, colour)  # raises for a slot the painted atlas lacks
        for kw in ({"subject": 0}, {"subject": 1}):
            P.build("kit_entrance_tapestry", **kw)
            P.build("kit_entrance_painting", **kw)
        P.build("kit_entrance_panel", case=0)
        P.build("kit_entrance_wall", stone=0)


class RoomTests(unittest.TestCase):
    def test_both_rooms_lint_clean_with_the_contract_layouts(self):
        for lid in ("lost_property_vault", "wishing_room_gathering"):
            r, (cb, b) = room(lid)
            rep = IK.lint(r, (cb, b))
            self.assertEqual(rep.errors, [], lid)
            self.assertLessEqual(A.budget_draws(cb), G.BUDGETS["classroom"].draws, lid)
            self.assertLessEqual(cb.triangles(), G.BUDGETS["classroom"].tris[1], lid)
            with open(os.path.join(INSTANCES, f"{lid}.json"), encoding="utf-8") as f:
                shipped = json.load(f)
                shipped.pop("solids", None)  # generated by world/solids.py (test_solids.py)
                self.assertEqual(shipped, json.loads(json.dumps(r.layout)), lid)
            self.assertEqual([s["id"] for s in r.layout["spawns"]], ["wishing_room_entry"])
            self.assertIn("wishing_room_exit", [i["id"] for i in r.layout["interactables"]])

    def test_the_heap_stands_behind_its_find_spot_and_the_aisle_is_clear(self):
        r, (cb, _b) = room("lost_property_vault")
        spot = next(i for i in r.layout["interactables"] if i["id"] == "find_lost_property_vault")
        heap = next(u for u in cb.uses if u.id == "hero:heap")
        self.assertLess(heap.origin[2], spot["pos"][2])
        self.assertLess(abs(heap.origin[0] - spot["pos"][0]), 0.5)
        # Nothing standing (but the heap behind the spot) in the aisle from the door to the spot.
        for u in cb.uses:
            if u.role != "hero" or u.id == "hero:heap" or u.piece.startswith("kit_lantern"):
                continue
            pl = geom.Placement(u.origin, u.yaw)
            for f in cb.pieces[u.id].faces:
                for p in (pl.point(q) for q in f.pts):
                    if p[1] < 2.0:
                        self.assertFalse(abs(p[0]) < 1.0 and spot["pos"][2] < p[2] < 8.0, (u.id, p))

    def test_the_gathering_room_seats_twenty(self):
        _r, (cb, _b) = room("wishing_room_gathering")
        # A seat row [x0, z0, x1, z1, y, yaw] seats its length // SEAT_WIDTH_MIN sitters (a chair one).
        sitters = sum(max(1, int(max(x1 - x0, z1 - z0) / G.SEAT_WIDTH_MIN + 1e-9)) for x0, z0, x1, z1, _y, _yaw, *_fit in cb.seats)
        self.assertGreaterEqual(sitters, 20)
        self.assertGreaterEqual(len(cb.floor_seats), 8)


class LeftoverTests(unittest.TestCase):
    def test_the_ravine_covers_the_shelf_under_the_bridge(self):
        cb = build("covered_bridge")
        self.assertEqual(LN.lint_cell(cb, builder()).errors, [])
        u = next(u for u in cb.uses if u.piece == "kit_ravine")
        import hlod_crag

        ground, _ = hlod_crag.backdrop_ground(os.path.join(ROOT, "data", "world", "terrain"))
        tris = [f.pts for f in cb.faces if f.src == u.id and f.normal()[1] > 0.2]
        for x in (60.0, 80.0, 96.0, 101.5, 104.0, 106.5, 110.0):
            for z in (76.0, 80.0, 84.0, 88.0, 91.0):
                ys = []
                for t in tris:
                    a, b, c = t[0], t[1], t[2]
                    d = (b[2] - c[2]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[2] - c[2])
                    if abs(d) < 1e-12:
                        continue
                    w0 = ((b[2] - c[2]) * (x - c[0]) + (c[0] - b[0]) * (z - c[2])) / d
                    w1 = ((c[2] - a[2]) * (x - c[0]) + (a[0] - c[0]) * (z - c[2])) / d
                    if w0 >= -1e-9 and w1 >= -1e-9 and 1 - w0 - w1 >= -1e-9:
                        ys.append(w0 * a[1] + w1 * b[1] + (1 - w0 - w1) * c[1])
                self.assertTrue(ys, (x, z))
                deck = 2.06 * (z - 74.0) / 18.0  # the bridge's deck (layout: y 0 at z 74 to 2.06 at z 92)
                if 100.0 < x < 108.0:  # under the deck: kept under it (it may dip under the backdrop's ground there)
                    self.assertLessEqual(max(ys), deck - 0.3 + 1e-6, (x, z))
                else:  # elsewhere over the backdrop's ground, so its grey shelf never shows
                    self.assertGreater(min(ys), ground(x, z) + 0.05 if z < 91.0 else ground(x, z) - 0.1, (x, z))

    def test_the_swing_nodes_corners_are_trimmed(self):
        layout = builder().layout
        for cell in R.GS_CELLS:
            cb = build(cell)
            self.assertEqual(LN.lint_cell(cb, builder()).errors, [], cell)
            for fl in R.moving_flights(layout, cell):
                s = layout.by_id(fl["a"])
                name = G.node_name(cell, "castle_props", R.swing_group(fl["id"]))
                md = cb.meshes[name]
                cx, cz = (s.x0 + s.x1) / 2, (s.z0 + s.z1) / 2
                for kx, kz in ((s.x0, s.z0), (s.x1, s.z0), (s.x1, s.z1), (s.x0, s.z1)):
                    ux, uz = (1.0 if cx > kx else -1.0), (1.0 if cz > kz else -1.0)
                    for v in md.verts:  # (world metres: the node's origin is applied at export)
                        self.assertGreaterEqual(ux * (v[0] - kx) + uz * (v[2] - kz), A.SWING_CHAMFER - 1e-3, (name, v))


if __name__ == "__main__":
    unittest.main()
