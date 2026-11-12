"""Unit tests for the scripted world art (tools/blender/world): the mesh
kit, the texture painter, the furniture models and atlas, and the dorm
shells with their generated instance layouts. Pure Python (numpy for the
painter); run through tests/run_tests.py."""

import json
import math
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.join(os.path.dirname(HERE), "world"), os.path.dirname(HERE)]

import atlas  # noqa: E402
import dorm_layouts  # noqa: E402
import dorms  # noqa: E402
import furniture_models as fm  # noqa: E402
import layout_lib as L  # noqa: E402
import meshkit as mk  # noqa: E402
import numpy as np  # noqa: E402
import paint  # noqa: E402

CATALOG = os.path.join(REPO, "data", "items", "catalog.json")


def centroid(pts):
    return tuple(sum(p[k] for p in pts) / len(pts) for k in range(3))


def _seg_dist(p, a, b) -> float:
    """Distance from 2D point p to segment ab."""
    ax, az = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * ax + (p[1] - a[1]) * az) / (ax * ax + az * az)))
    return math.hypot(p[0] - a[0] - t * ax, p[1] - a[1] - t * az)


def furniture_items():
    with open(CATALOG, encoding="utf-8") as f:
        return [i for i in json.load(f)["items"] if i["kind"] == "furniture"]


class MeshKitTests(unittest.TestCase):
    def assert_outward(self, m, centre):
        for f in m.faces:
            pts = m.face_points(f)
            n = mk.newell(pts)
            self.assertGreater(mk.dot(n, mk.sub(centroid(pts), centre)), -1e-9, f)

    def test_box_and_lathe_face_outward(self):
        m = mk.Mesh()
        mk.box(m, (0, 0, 0), (1, 2, 3), "x")
        self.assertEqual(m.triangle_count(), 12)
        self.assert_outward(m, (0.5, 1.0, 1.5))
        m = mk.Mesh()
        mk.lathe(m, [(0.5, 0.0), (0.5, 1.0)], 8, "x", cap_top=True, cap_bottom=True)
        self.assert_outward(m, (0.0, 0.5, 0.0))
        lo, hi = m.bounds()
        self.assertAlmostEqual(hi[1], 1.0)
        self.assertLessEqual(hi[0], 0.5 + 1e-9)

    def test_rod_spans_its_endpoints(self):
        m = mk.Mesh()
        mk.rod(m, (0, 0, 0), (1, 1, 1), 0.05, 6, "x")
        lo, hi = m.bounds()
        for k in range(3):
            self.assertAlmostEqual(lo[k], -0.05, delta=0.05)
            self.assertAlmostEqual(hi[k], 1.05, delta=0.05)
        self.assert_outward(m, (0.5, 0.5, 0.5))

    def test_arch_outlines(self):
        for shape in ("pointed", "round", "flat", "circle"):
            out = mk.arch_outline(1.2, 2.0, shape, 0.5)
            self.assertGreater(mk.polygon_area(out), 0, shape)  # counter-clockwise
        pointed = mk.arch_outline(1.0, 2.0, "pointed", 0.0)
        self.assertAlmostEqual(max(y for _, y in pointed), 2.0 + 0.55, places=6)
        self.assertAlmostEqual(max(x for x, _ in pointed), 0.5)
        circle = mk.arch_outline(1.0, 1.0, "circle")
        self.assertAlmostEqual(min(y for _, y in circle), 0.5, places=6)

    def test_place_rotates_about_y(self):
        m = mk.Mesh()
        mark = m.mark()
        mk.box(m, (-0.1, 0, 0.9), (0.1, 0.1, 1.1), "x")
        m.place(mark, math.pi / 2, (5, 0, 0))
        lo, hi = m.bounds()
        # Local +Z ends up along +X after a quarter turn.
        self.assertAlmostEqual((lo[0] + hi[0]) / 2, 6.0, places=6)


class PaintTests(unittest.TestCase):
    def test_tiling_and_determinism(self):
        for name, img in {
            "masonry": paint.masonry(256, "#9A8F7E", "#5A534A", [64, 48], (60, 120), 3),
            "planks": paint.planks(256, "#7A5434", 6, 4),
            "plaster": paint.plaster(128, "#DCCBA2", 5),
            "velvet": paint.velvet(128, "#8A1414", 6),
            "weave": paint.weave(128, "#E6DFCF", 7),
        }.items():
            self.assertEqual(img.shape[2], 4, name)
            self.assertTrue(paint.is_tileable(img), name)
        a = paint.masonry(128, "#9A8F7E", "#5A534A", [32], (30, 60), 9)
        b = paint.masonry(128, "#9A8F7E", "#5A534A", [32], (30, 60), 9)
        self.assertTrue(np.array_equal(a, b))

    def test_albedo_stays_in_the_style_guide_range(self):
        # No pure black or white surfaces (#383838 .. #ECECEC on average).
        for img in (paint.masonry(256, "#9A8F7E", "#5A534A", [64], (60, 120), 3), paint.planks(256, "#7A5434", 6, 4)):
            lum = img[..., :3].mean(axis=2)
            self.assertGreater(float(np.percentile(lum, 5)), 0.12)
            self.assertLess(float(np.percentile(lum, 95)), 0.93)


class FurnitureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = furniture_items()
        cls.models = {i["asset"]: fm.build(i) for i in cls.items}

    def test_every_catalog_item_has_a_model(self):
        self.assertTrue(self.items)
        for item in self.items:
            self.assertIn(item["asset"], fm.MODELS, item["name"])

    def test_models_match_their_catalog_footprint(self):
        for item in self.items:
            f = item["furniture"]
            m = self.models[item["asset"]]
            (x0, y0, z0), (x1, y1, z1) = m.bounds()
            w, d = f["footprint"]
            tol = 0.03
            self.assertAlmostEqual(x1 - x0, w, delta=tol + 0.02 * w, msg=item["name"])
            self.assertAlmostEqual((x0 + x1) / 2, 0.0, delta=tol, msg=item["name"])
            self.assertGreaterEqual(y0, -1e-6, item["name"])
            self.assertLessEqual(y1, f["height"] + tol, item["name"])
            self.assertGreater(y1, f["height"] * 0.85, item["name"])
            if f["surface"] == "wall":
                # Back on z = 0, hanging into the room.
                self.assertGreaterEqual(z0, -1e-6, item["name"])
                self.assertLessEqual(z1, d + tol, item["name"])
            else:
                self.assertAlmostEqual(z1 - z0, d, delta=tol + 0.02 * d, msg=item["name"])
                self.assertAlmostEqual((z0 + z1) / 2, 0.0, delta=tol, msg=item["name"])
            if "top" in f:
                # Nothing but the gallery rail rises above a table top.
                tops = [p[1] for p in m.verts]
                self.assertLessEqual(max(tops), f["height"] + 1e-6, item["name"])

    def test_seats_say_how_far_the_model_reaches(self):
        """Seats fit the sitter's legs (docs/architecture.md): a catalog
        seat's `front` is the furthest the model reaches ahead of the seat
        area's centre line along its facing, under the top and over the
        floor, within the area's width (the frame or the cushion); `back` is
        the backrest's face behind it, or a backless seat's back edge (the
        top's furthest point behind); `drop` the top over the floor."""
        seated = 0
        for item in self.items:
            m = self.models[item["asset"]]
            for s in item["furniture"].get("seats", []):
                seated += 1
                name = item["name"]
                x0, z0, x1, z1 = s["area"]
                y = s["y"]
                yaw = s.get("yaw", 0.0)
                cx, cz = (x0 + x1) / 2, (z0 + z1) / 2
                fx, fz = math.sin(yaw), math.cos(yaw)
                across = abs((x1 - x0) * fz) / 2 + abs((z1 - z0) * fx) / 2
                front, rest, edge = 0.0, None, 0.0
                for face in m.faces:
                    # The seat's frame: x across, +z the way the sitter faces.
                    pts = [((p[0] - cx) * fz - (p[2] - cz) * fx, p[1], (p[0] - cx) * fx + (p[2] - cz) * fz)
                           for p in (m.verts[i] for i in face.idx)]
                    lo = [min(q[k] for q in pts) for k in range(3)]
                    hi = [max(q[k] for q in pts) for k in range(3)]
                    if hi[0] <= -across + 1e-3 or lo[0] >= across - 1e-3:
                        continue  # beside the seat (arms, posts, the curtains)
                    if hi[1] > 0.02 and lo[1] < y - 1e-3:
                        front = max(front, hi[2])
                    if hi[1] > y + 0.05 and lo[1] < y + 0.6 and hi[2] < 0.0:
                        rest = hi[2] if rest is None else max(rest, hi[2])
                    if hi[1] >= y - 0.015 and lo[1] <= y + 0.1:
                        edge = max(edge, -lo[2])
                back = -rest if rest is not None else edge
                self.assertAlmostEqual(s["front"], front, delta=2e-3, msg=name)
                self.assertAlmostEqual(s["back"], back, delta=2e-3, msg=name)
                self.assertAlmostEqual(s["drop"], y, delta=1e-6, msg=name)  # the feet on the room's floor
        self.assertEqual(seated, 5)  # the bed's two sides, the chair, the armchair, the stool

    def test_dye_zones_and_budget(self):
        for asset, m in self.models.items():
            self.assertTrue(m.zones() <= {0, 1, 2, 3}, asset)
            self.assertIn(1, m.zones(), asset)
            self.assertLessEqual(m.triangle_count(), 1500, asset)
            for mat in m.materials():
                self.assertIn(atlas.ALIASES.get(mat, mat), atlas.TILES, (asset, mat))

    def test_atlas_uvs_stay_in_their_tiles(self):
        for asset, m in self.models.items():
            for f, uvs in zip(m.faces, atlas.atlas_uvs(m)):
                (x, y, w, h), _ = atlas.TILES[atlas.ALIASES.get(f.material, f.material)]
                for u, v in uvs:
                    self.assertGreaterEqual(u * atlas.SIZE, x - 1e-6, asset)
                    self.assertLessEqual(u * atlas.SIZE, x + w + 1e-6, asset)
                    self.assertGreaterEqual(v * atlas.SIZE, y - 1e-6, asset)
                    self.assertLessEqual(v * atlas.SIZE, y + h + 1e-6, asset)
        masks = atlas.face_masks(self.models["furniture/bed_four_poster_a"])
        self.assertIn((1.0, 0.0, 0.0), masks)
        self.assertIn((0.0, 0.0, 0.0), masks)  # the undyed linen

    def test_atlas_paints_every_tile(self):
        img = atlas.paint_atlas()
        self.assertEqual(img.shape, (atlas.SIZE, atlas.SIZE, 4))
        for name, ((x, y, w, h), _) in atlas.TILES.items():
            self.assertGreater(float(img[y : y + h, x : x + w, :3].mean()), 0.05, name)


class DormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shells = dorms.build_all()
        cls.ids = dorm_layouts.layout_ids()

    def test_one_shell_per_house_with_neutral_cells(self):
        self.assertEqual(list(self.ids.values()), [f"dorm_{h}" for h in L.known_houses()])
        for slot, shell in self.shells.items():
            self.assertEqual(shell.cell, f"dorm_{slot}")

    def test_shell_budgets_and_materials(self):
        for slot, shell in self.shells.items():
            self.assertLessEqual(shell.mesh.triangle_count(), 8000, slot)
            used = shell.mesh.materials()
            self.assertLessEqual(len(used), 20, slot)  # draw calls
            for mat in used:
                self.assertIn(mat, shell.materials, (slot, mat))
            self.assertTrue(any(s.emissive > 0 for s in shell.materials.values()), slot)

    def test_faces_look_into_the_room(self):
        # Wall faces (on the inner outline) face the room; roofs face down.
        for slot, shell in self.shells.items():
            poly = shell.outline
            bad = []
            for f in shell.mesh.faces:
                pts = shell.mesh.face_points(f)
                c = centroid(pts)
                n = mk.newell(pts)
                on_wall = min(_seg_dist((c[0], c[2]), poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))) < 0.02
                if f.material in ("stone", "plaster") and on_wall and abs(n[1]) < 1e-6:
                    if mk.dot(n, (-c[0], 0.0, -c[2])) <= 0:
                        bad.append(c)
                if f.material in ("roof_plaster", "ceiling", "dome", "vault") and n[1] > 1e-6:
                    bad.append(c)
            self.assertEqual(bad, [], slot)

    def test_lights_are_inside_the_room(self):
        for slot, shell in self.shells.items():
            lo, hi = shell.mesh.bounds()
            self.assertGreaterEqual(len(shell.lights), 3, slot)
            for lt in shell.lights:
                for k in range(3):
                    self.assertTrue(lo[k] - 0.5 <= lt["pos"][k] <= hi[k] + 0.5, (slot, lt))
                self.assertRegex(lt["color"], r"^#[0-9A-Fa-f]{6}$")
                self.assertGreater(lt["intensity"], 0)
                self.assertGreater(lt["range"], 0)

    def test_instance_files_are_up_to_date(self):
        for lid, text in dorm_layouts.generate().items():
            path = os.path.join(REPO, "data", "world", "instances", f"{lid}.json")
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), text, f"{path}: run tools/blender/world/dorm_layouts.py --write")

    def test_instance_layouts_are_usable(self):
        bed = (1.6, 2.3)
        for slot, shell in self.shells.items():
            data = dorm_layouts.layout_for(shell)
            lay = L.parse_layout(data)
            rep = L.validate(lay)
            self.assertEqual(rep.errors, [], slot)
            areas = {a.id: a for a in lay.place_areas}
            self.assertIn("floor", areas)
            self.assertIn("wall_north", areas)
            # The sim-core test: a bed at (0, 0, -2) and a banner on the north wall.
            fits = any(a.surface == "floor" and a.min[0] <= -bed[0] / 2 and a.max[0] >= bed[0] / 2
                       and a.min[2] <= -2 - bed[1] / 2 and a.max[2] >= -2 + bed[1] / 2 for a in lay.place_areas)
            self.assertTrue(fits, slot)
            north = areas["wall_north"]
            self.assertLessEqual(north.min[1], 1.0)
            self.assertGreaterEqual(north.max[1], 2.6)
            self.assertLessEqual(north.min[0], -0.45)
            self.assertGreaterEqual(north.max[0], 0.45)
            # Floor areas avoid the fixed furniture and stay inside the walls.
            poly = shell.outline
            for a in lay.place_areas:
                if a.surface != "floor":
                    continue
                for ob in shell.obstacles:
                    ox = min(a.max[0], ob[2]) - max(a.min[0], ob[0])
                    oz = min(a.max[2], ob[3]) - max(a.min[2], ob[1])
                    self.assertFalse(ox > 1e-6 and oz > 1e-6, (slot, a.id, ob))
                for x, z in ((a.min[0], a.min[2]), (a.max[0], a.max[2]), (a.min[0], a.max[2]), (a.max[0], a.min[2])):
                    self.assertTrue(dorm_layouts._inside_convex(poly, x, z), (slot, a.id))
            # Wall areas lie on a wall of the room: its outermost extent on
            # that axis (a flat cardinal facet, or a side of the dungeon).
            for a in lay.place_areas:
                if a.surface == "wall":
                    on_x = a.wall_on_x_plane
                    coord = a.min[0] if on_x else a.min[2]
                    extent = max(abs(p[0 if on_x else 1]) for p in poly)
                    self.assertAlmostEqual(abs(coord), extent, places=2, msg=(slot, a.id))
            # The way out and the spawn stand on walkable ground.
            ex = lay.interactables[0]
            self.assertIsNotNone(L.interactable_surface(lay, ex))
            self.assertIsNotNone(L.spawn_surface(lay, lay.spawns[0]["pos"]))


if __name__ == "__main__":
    unittest.main()
