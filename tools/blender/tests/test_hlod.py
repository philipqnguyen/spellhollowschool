"""The far castle silhouette (docs/art/castle-kit.md "Exterior",
world/hlod_build.py): the chunks of data/world/exterior.json within their
budgets, on the two library materials, grey COLOR_0, deterministic; the
stand-ins 0.3 m inside the real cells' outer faces; the manifest entry; the
export when it exists. Pure Python but the last class (reads the glb).

The skyline: the lit panes, the steep Great Hall roof over the real
one, the crag chunk's rock clear of every playable space and the grounds,
within the 3a budgets (`P3bSkylineTests`).

Stable courtyard walls (2026-11): no silhouette face lies flush with (within
P3A_HLOD_INSET of) a playable cell's wall facing the same way, where the two
would z-fight (`hlod_build.coplanar_overlaps`)."""

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
from castle_kit import grid as G  # noqa: E402
from castle_kit import schema as S  # noqa: E402
from castle_kit.pieces import geom  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
EXTERIOR = os.path.join(ROOT, "data", "world", "exterior.json")
WORLD = os.path.join(ROOT, "client", "public", "assets", "world")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_BUILT = {}


def built():
    if not _BUILT:
        layout = L.load_layout(S.LAYOUT_PATH)
        plan, rep = S.resolve(S.load_json(S.KIT_PATH), layout, S.PROPS_PATH, S.load_texture_set())
        assert rep.ok, rep.errors
        ext = load(EXTERIOR)
        _BUILT.update(layout=layout, plan=plan, ext=ext, chunks=HB.build(layout, ext, plan))
    return _BUILT


class HlodBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        b = built()
        cls.layout, cls.plan, cls.ext, cls.chunks = b["layout"], b["plan"], b["ext"], b["chunks"]

    def test_within_budgets(self):
        self.assertEqual(HB.report(self.chunks, self.ext), [])
        n = len(self.ext["chunks"])
        self.assertTrue(G.P3A_HLOD_CHUNKS[0] <= n <= G.P3A_HLOD_CHUNKS[1])
        tris = sum(md.triangles() for m in self.chunks.values() for md in m.values())
        self.assertLessEqual(tris, G.P3A_HLOD_TRIS)
        mb = HB.gpu_bytes([md for m in self.chunks.values() for md in m.values()]) / (1024 * 1024)
        self.assertLessEqual(mb, G.P3A_HLOD_GPU_MB)

    def test_every_chunk_drawn_on_the_library_materials(self):
        self.assertEqual(list(self.chunks), [c["id"] for c in self.ext["chunks"]])
        for cid, meshes in self.chunks.items():
            self.assertTrue(meshes, cid)
            self.assertLessEqual(len(meshes), G.P3A_HLOD_DRAWS_PER_CHUNK, cid)
            for mat, md in meshes.items():
                if cid == self.ext["crag"]["chunk"]:
                    self.assertEqual(mat, self.ext["crag"]["material"])  # The crag's rock
                    continue
                self.assertIn(mat, ("castle_wall_c@ext", "castle_roof"))  # the one outside stone: the west range's
                self.assertEqual(md.name, G.hlod_node(cid, mat))
                self.assertEqual(len(md.uvs), len(md.faces))
                self.assertEqual(len(md.colors), len(md.faces))
        self.assertEqual(HB.STONE, G.ext_name(G.P3A_EXTERIOR_STONE))

    def test_colour0_grey_and_windows(self):
        panes = 0
        for meshes in self.chunks.values():
            for md in meshes.values():
                for face in md.colors:
                    for c in face:
                        self.assertTrue(c[0] == c[1] == c[2] and 0.0 <= c[0] <= 1.0, c)
                        panes += abs(c[0] - HB.SHADE_PANE) < 1e-6 or abs(c[0] - HB.SHADE_LIT) < 1e-6
        self.assertGreater(panes, 1000)  # the window bands

    def test_faces_planar_and_facing_out(self):
        for meshes in self.chunks.values():
            for md in meshes.values():
                for f in md.faces:
                    pts = [md.verts[i] for i in f]
                    n = geom.newell(pts)
                    self.assertGreater(geom.norm(n), 1e-9)
                    u = geom.unit(n)
                    self.assertTrue(all(abs(geom.dot(u, geom.sub(p, pts[0]))) < 1e-6 for p in pts))

    def test_deterministic(self):
        again = HB.build(self.layout, self.ext, self.plan)
        for cid, meshes in self.chunks.items():
            for mat, md in meshes.items():
                other = again[cid][mat]
                self.assertEqual(md.verts, other.verts)
                self.assertEqual(md.faces, other.faces)
                self.assertEqual(md.colors, other.colors)

    def test_stand_ins_sit_inside_the_real_walls(self):
        """Every outward Planner wall face of a stand-in's cell has its copy
        P3A_HLOD_INSET behind it, and no stand-in vertex lies outside the
        cell's outer planes' box."""
        plan = HB.layout_plan(self.layout)
        d = G.P3A_HLOD_INSET
        for ch in self.ext["chunks"]:
            if not ch["cells"]:
                continue
            md = self.chunks[ch["id"]][HB.STONE]
            planes = set()
            for f in md.faces:
                pts = [md.verts[i] for i in f]
                n = geom.unit(geom.newell(pts))
                for k in (0, 2):
                    if abs(abs(n[k]) - 1.0) < 1e-6:
                        planes.add((k, round(pts[0][k], 3), 1.0 if n[k] > 0 else -1.0))
            for cell in ch["cells"]:
                for poly in plan.polys.get((cell, "wall_exterior"), []):
                    n = geom.unit(geom.newell(poly))
                    for k in (0, 2):
                        if abs(abs(n[k]) - 1.0) < 1e-6:
                            sign = 1.0 if n[k] > 0 else -1.0
                            self.assertIn((k, round(poly[0][k] - sign * d, 3), sign), planes, (cell, poly[0]))
            pts = [p for cell in ch["cells"] for poly in plan.polys.get((cell, "wall_exterior"), []) for p in poly]
            # (The gatehouse runs back over the courtyard's wall and beyond its outer face.)
            for cell in ch["cells"]:
                for (x0, z0, x1, z1), y0, y1 in HB.gatehouse_boxes(self.plan, cell, 0.0):
                    pts += [(x0, y0, z0), (x1, y1, z1)]
            if not pts:
                continue  # A shell the kit draws (the Owl Loft; test_p3c_wings_b checks its chunk)
            lo = [min(p[k] for p in pts) for k in range(3)]
            hi = [max(p[k] for p in pts) for k in range(3)]
            for v in md.verts:
                for k in (0, 2):
                    self.assertTrue(lo[k] - 1e-6 <= v[k] <= hi[k] + 1e-6, (ch["id"], v))

    def test_facade_roofs_under_the_real_roofs(self):
        """A stand-in's pitched roof stays under the facade roof's underside."""
        for cell in ("entrance_hall", "great_hall"):
            frame = HB.facade_roof_frame(self.layout, self.plan, cell)
            self.assertIsNotNone(frame, cell)
            x0, z0, x1, z1 = frame["rect"]
            tan = math.tan(math.radians(frame["pitch"]))
            cid = next(c["id"] for c in self.ext["chunks"] if cell in c["cells"])
            md = self.chunks[cid][HB.ROOF]
            for v in md.verts:
                if not (x0 <= v[0] <= x1 and z0 <= v[2] <= z1):
                    continue
                off = (v[2] - (z0 + z1) / 2.0) if frame["along_x"] else (v[0] - (x0 + x1) / 2.0)
                half = ((z1 - z0) if frame["along_x"] else (x1 - x0)) / 2.0
                under = frame["top"] + (half - abs(off)) * tan
                self.assertLessEqual(v[1], under + 1e-6, (cell, v))

    def test_masses_and_caps(self):
        """Every mass is drawn; a cap stands on its cell's highest roof; the
        Astronomy Tower's spire is the highest point."""
        top = max(v[1] for m in self.chunks.values() for md in m.values() for v in md.verts)
        astro = next(m for m in self.ext["masses"] if m["id"] == "astronomy_tower")
        self.assertAlmostEqual(top, astro["top"] + astro["roof_height"], places=6)
        for m in self.ext["masses"]:
            if "over" in m:
                roof = HB.cell_roof(self.layout, m["over"])
                md = self.chunks[m["chunk"]][HB.ROOF]
                (cx, cz) = m["centre"] if m["shape"] == "round" else ((m["min"][0] + m["max"][0]) / 2,
                                                                     (m["min"][1] + m["max"][1]) / 2)
                near = [v for v in md.verts if abs(v[0] - cx) < 0.01 and abs(v[2] - cz) < 0.01]
                if m["roof"] in ("cone", "pyramid"):
                    self.assertTrue(near, m["id"])
                    self.assertAlmostEqual(max(v[1] for v in near), roof + m.get("rise", 0.0) + m["roof_height"], places=6)

    def test_no_face_coplanar_with_a_real_wall(self):
        """No silhouette wall faces the same way as a playable cell's wall
        within P3A_HLOD_INSET of its plane (they would z-fight wherever both
        are drawn: the Clock Tower ranges flush with the Clock Tower
        Courtyard's walls showed their pale stone through them in blocks)."""
        self.assertEqual(HB.coplanar_overlaps(self.chunks, HB.layout_plan(self.layout)), [])

    def test_coplanar_lint_catches_a_flush_mass(self):
        """The lint flags a mass flush with a courtyard's wall (the Clock Tower
        range as it was, 2026-11) and passes it set P3A_HLOD_INSET back."""
        planner = HB.layout_plan(self.layout)

        def rows(max_z):
            ext = {"chunks": [{"id": "hlod_test", "name": "test", "cells": []}],
                   "masses": [{"id": "flush", "chunk": "hlod_test", "shape": "box", "min": [60, 36], "max": [104, max_z],
                               "base": -2, "top": 22, "roof": "flat", "windows": False}]}
            return HB.coplanar_overlaps(HB.build(self.layout, ext, self.plan), planner)

        flush = rows(50.0)
        self.assertTrue(flush)
        self.assertEqual({(r[0], r[1]) for r in flush}, {("hlod_test", "clock_courtyard")})
        self.assertEqual(flush[0][3], (0.0, 0.0, 1.0))
        self.assertEqual(rows(50.0 - G.P3A_HLOD_INSET), [])

    def test_manifest_entry(self):
        e = HB.manifest_entry(self.chunks, self.ext)
        self.assertEqual(e["file"], G.P3A_HLOD_FILE)
        self.assertEqual([c["id"] for c in e["chunks"]], [c["id"] for c in self.ext["chunks"]])
        self.assertEqual(e["tris"], sum(c["tris"] for c in e["chunks"]))
        for row, ch in zip(e["chunks"], self.ext["chunks"]):
            self.assertEqual(set(row), {"id", "name", "cells", "bounds", "tris", "draws", "library"})
            self.assertEqual(row["cells"], ch["cells"])
            self.assertTrue(all(row["bounds"]["min"][k] < row["bounds"]["max"][k] for k in range(3)))

    def test_no_colour_literals(self):
        for name in ("hlod_build.py", "hlod_export.py", "hlod_stills.py"):
            with open(os.path.join(os.path.dirname(HERE), "world", name), encoding="utf-8") as f:
                self.assertEqual(G.colour_literals(f.read()), [], name)


class HlodHelperTests(unittest.TestCase):
    def test_window_columns_and_bands(self):
        self.assertEqual(HB.window_columns(2.0), [])
        cols = HB.window_columns(30.0)
        self.assertTrue(all(HB.WINDOW_MARGIN - 1e-9 <= a and b <= 30.0 - HB.WINDOW_MARGIN + 1e-9 for a, b in cols))
        self.assertAlmostEqual(cols[0][0], 30.0 - cols[-1][1])
        bands = HB.window_bands(0.0, 27.0)
        self.assertEqual(bands, [(3.0, 6.6), (12.0, 15.6), (21.0, 24.6)])
        self.assertEqual(HB.window_bands(0.0, 4.0), [])

    def test_drum_faces_the_lake(self):
        ring = HB.drum_outline(0.0, 0.0, 6.0)
        edges = HB.edges_out(ring)
        self.assertEqual(len(edges), HB.DRUM_SIDES)
        self.assertTrue(any(abs(n[2] - 1.0) < 1e-9 for _, _, n in edges))  # a flat face toward +z
        for a, b, _ in edges:
            mid = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
            self.assertAlmostEqual(math.hypot(*mid), 6.0)

    def test_grow(self):
        self.assertEqual(HB.grow(HB.box_outline(0, 0, 4, 2), 0.5), HB.box_outline(-0.5, -0.5, 4.5, 2.5))
        ring = HB.grow(HB.drum_outline(1.0, 2.0, 6.0), 0.5)
        for a, b, _ in HB.edges_out(ring):
            self.assertAlmostEqual(math.hypot((a[0] + b[0]) / 2.0 - 1.0, (a[1] + b[1]) / 2.0 - 2.0), 6.5)


class P3bSkylineTests(unittest.TestCase):
    """The night windows, the steep Great Hall roof, the
    crag, the budgets."""

    @classmethod
    def setUpClass(cls):
        b = built()
        cls.layout, cls.plan, cls.ext, cls.chunks = b["layout"], b["plan"], b["ext"], b["chunks"]

    def test_budgets_with_the_crag(self):
        draws = sum(len(m) for m in self.chunks.values())
        self.assertLessEqual(draws, G.P3C_HLOD_DRAWS_MAX)  # the whole castle: 16 draws (+ its chunks)
        self.assertEqual(len(self.chunks[self.ext["crag"]["chunk"]]), 1)
        self.assertEqual(HB.report(self.chunks, self.ext), [])

    def test_some_panes_lit_by_night(self):
        lit = unlit = 0
        for cid, meshes in self.chunks.items():
            md = meshes.get(HB.STONE)
            if md is None:
                continue
            for face in md.colors:
                g = face[0][0]
                if all(c[0] == g for c in face):
                    lit += abs(g - HB.SHADE_LIT) < 1e-6
                    unlit += abs(g - HB.SHADE_PANE) < 1e-6
        share = lit / float(lit + unlit)
        self.assertTrue(0.3 <= share <= 0.55, share)
        # The client tells them apart in sRGB (castle/hlod.ts LIT_PANE_BELOW 0.54), every stone face above both.
        srgb = lambda v: (round(v * 255) / 255.0) ** (1 / 2.2)  # noqa: E731
        self.assertLess(srgb(HB.SHADE_LIT), 0.54)
        self.assertGreater(srgb(HB.SHADE_PANE), 0.54)
        self.assertGreater(srgb(min(HB.SHADE_FOOT, HB.SHADE_SOFFIT)), srgb(HB.SHADE_PANE))

    def test_steep_roofs_over_the_real_ones(self):
        """A steep roof's slopes stand on or over the real facade roof's top
        everywhere over its rectangle, and it is steeper."""
        for spec in self.ext["steep_roofs"]:
            frame = HB.facade_roof_frame(self.layout, self.plan, spec["cell"])
            self.assertIsNotNone(frame)
            self.assertGreater(spec["pitch"], frame["pitch"])
            x0, z0, x1, z1 = frame["rect"]
            tan = math.tan(math.radians(frame["pitch"]))
            half = ((z1 - z0) if frame["along_x"] else (x1 - x0)) / 2.0
            md = self.chunks[spec["chunk"]][HB.ROOF]
            steep_tan = math.tan(math.radians(spec["pitch"]))
            eave = frame["top"] + HB.STEEP_LIFT

            def steep(off):
                return eave + (half + HB.STEEP_GROW - abs(off)) * steep_tan

            # The steep roof's own corners (on its slopes) are in the mesh ...
            on = [v for v in md.verts if x0 - 0.2 <= v[0] <= x1 + 0.2 and z0 - 0.2 <= v[2] <= z1 + 0.2
                  and abs(v[1] - steep((v[2] - (z0 + z1) / 2.0) if frame["along_x"] else (v[0] - (x0 + x1) / 2.0))) < 1e-4]
            self.assertGreaterEqual(len(on), 6, spec["cell"])
            # ... and its slopes stand over the real roof's everywhere across it.
            for k in range(41):
                off = -half + 2.0 * half * k / 40.0
                real = frame["top"] + (half - abs(off)) * tan
                self.assertGreaterEqual(steep(off), real, (spec["cell"], off))

    def test_crag_clear_of_playable_spaces_and_the_grounds(self):
        crag = self.ext["crag"]
        md = self.chunks[crag["chunk"]][crag["material"]]
        box = load(os.path.join(ROOT, "data", "world", "terrain", "grounds.json"))["box"]
        castle = [s for s in self.layout.surfaces if s.kind != "grounds"]
        self.assertGreater(len(md.faces), 100)
        for v in md.verts:
            x, y, z = v
            in_box = box["min"][0] - 1e-6 <= x <= box["max"][0] + 1e-6 and box["min"][1] - 1e-6 <= z <= box["max"][1] + 1e-6
            self.assertFalse(in_box, v)
            for s in castle:
                inside = s.x0 - 0.3 < x < s.x1 + 0.3 and s.z0 - 0.3 < z < s.z1 + 0.3
                self.assertFalse(inside and s.floor_min - 1.0 < y < s.struct_top(), (s.id, v))
        for face in md.colors:
            for c in face:
                self.assertTrue(c[0] == c[1] == c[2] and 0.0 <= c[0] <= 1.0)

    def test_crag_colour_is_a_palette_row(self):
        pal = load(os.path.join(ROOT, "data", "art", "castle_textures.json"))["palette"]
        self.assertIn(self.ext["crag"]["colour"], pal)
        self.assertTrue(self.ext["crag"]["material"].startswith("backdrop_"))  # the backdrop's toon row

    def test_vertical_variety(self):
        """The skyline's spires span a wide range of heights."""
        tops = sorted(m["top"] + m.get("roof_height", 0) for m in self.ext["masses"] if "top" in m)
        self.assertGreaterEqual(tops[-1] - tops[0], 70)
        self.assertGreaterEqual(len({round(t / 6.0) for t in tops}), 10)


def _glb(path: str):
    with open(path, "rb") as f:
        data = f.read()
    n = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + n])


@unittest.skipUnless(os.path.exists(os.path.join(WORLD, G.P3A_HLOD_FILE)), "no hlod.glb exported")
class HlodExportTests(unittest.TestCase):
    """The export (greybox.py with world/hlod_export.py): hlod.glb's nodes and
    the manifest's entry match the build."""

    def test_glb_and_manifest(self):
        doc = _glb(os.path.join(WORLD, G.P3A_HLOD_FILE))
        manifest = load(os.path.join(WORLD, "manifest.json"))
        self.assertIn("hlod", manifest)
        entry = manifest["hlod"]
        names = sorted(n["name"] for n in doc["nodes"] if "mesh" in n)
        want = sorted(G.hlod_node(c["id"], m) for c in entry["chunks"] for m in c["library"])
        self.assertEqual(names, want)
        mats = {m["name"] for m in doc["materials"]}
        self.assertLessEqual(mats, {"castle_wall_c@ext", "castle_roof", built()["ext"]["crag"]["material"]})
        self.assertEqual(entry, HB.manifest_entry(built()["chunks"], built()["ext"]))
        for mesh in doc["meshes"]:
            for p in mesh["primitives"]:
                self.assertIn("COLOR_0", p["attributes"])
                self.assertEqual(doc["accessors"][p["attributes"]["COLOR_0"]]["componentType"], 5121)


if __name__ == "__main__":
    unittest.main()
