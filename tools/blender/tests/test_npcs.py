"""Unit tests for the NPCs' art (docs/art/castle-kit.md "Portraits and tapestries"; docs/architecture.md
"NPCs"): the talking portraits' placements (sim-core's twin), their frames
(budgets, the box's depth, tags and UVs), the four painted backdrops
(palette names, determinism), the decorative clusters' keep-out round every
talking frame, each frame on solid wall clear of the built castle, and
every NPC's look (adults' catalog clothing the body may wear, a shipped hair
style, palette dyes, students in their house's school robe). Pure Python;
run through tests/run_tests.py (the built-castle checks build the cells the
portraits hang in)."""

import json
import math
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path[:0] = [os.path.dirname(HERE), os.path.join(os.path.dirname(HERE), "world")]

import layout_lib as L  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import props_atlas as PA  # noqa: E402
from castle_kit import rules as R  # noqa: E402
from npcs import backdrops as BD  # noqa: E402
from npcs import frames as FR  # noqa: E402
from npcs import placements as PL  # noqa: E402

LAYOUT = os.path.join(REPO, "data", "world", "layout.json")
NPCS_DIR = os.path.join(REPO, "data", "npcs")
ASSETS = os.path.join(REPO, "client", "public", "assets", "npcs")


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_LAYOUT = None
_BUILDER = None


def layout():
    global _LAYOUT
    if _LAYOUT is None:
        _LAYOUT = L.load_layout(LAYOUT)
    return _LAYOUT


def builder():
    global _BUILDER
    if _BUILDER is None:
        from castle_kit import assemble as A

        _BUILDER = A.Builder.from_files()
    return _BUILDER


class PlacementTests(unittest.TestCase):
    def test_every_roster_portrait_hangs_once_by_sim_cores_rules(self):
        placed = PL.resolve(layout())
        roster = PL.roster_portraits()
        self.assertEqual(sorted(p.npc for p in placed), sorted(roster))
        self.assertTrue(15 <= len(placed) <= 20)
        for p in placed:
            self.assertIn(p.backdrop, range(BD.COUNT))
            self.assertTrue(PL.SIZE_W[0] <= p.size[0] <= PL.SIZE_W[1] and PL.SIZE_H[0] <= p.size[1] <= PL.SIZE_H[1])
        for a in placed:
            for b in placed:
                if a.npc < b.npc and a.level == b.level:
                    self.assertGreaterEqual(math.hypot(a.pos[0] - b.pos[0], a.pos[2] - b.pos[2]), PL.APART_M)

    def test_resolve_refuses_what_sim_core_refuses(self):
        good = PL.load()[0]
        roster = [good["npc"]]
        for bad in ({"t": 0.1}, {"size": [2.0, 1.0]}, {"y": 0.5}, {"backdrop": 4}, {"surface": "nowhere"}):
            with self.assertRaises(ValueError, msg=bad):
                PL.resolve(layout(), [dict(good, **bad)], roster)
        with self.assertRaises(ValueError):
            PL.resolve(layout(), [good, good], roster)

    def test_the_face_matches_the_castle_kits_wall_lines(self):
        """A placement's (axis, c, sign) is the castle kit's side_line of its edge."""
        sides = {"z_min": "n", "z_max": "s", "x_min": "w", "x_max": "e"}
        by_id = {s.id: s for s in layout().surfaces}
        for p in PL.resolve(layout()):
            axis, c, sign, _ = G.side_line(by_id[p.surface], sides[p.edge])
            entry = next(e for e in PL.load() if e["npc"] == p.npc)
            self.assertEqual((p.axis, p.sign), (axis, sign))
            self.assertAlmostEqual(p.c, c - sign * entry.get("inset", 0.0), places=6)
            self.assertAlmostEqual(p.yaw, R.yaw_for(axis, sign), places=6)


class FrameTests(unittest.TestCase):
    def sizes(self):
        return sorted({tuple(e["size"]) for e in PL.load()})

    def test_budgets(self):
        for s in self.sizes():
            self.assertLessEqual(FR.lod0_triangles(s), FR.TRIS_LOD0, s)
            self.assertLessEqual(FR.triangles(FR.lod1_faces(s)), FR.TRIS_LOD1, s)

    def test_the_box_is_the_contracts_depth_and_inside_the_outer_extent(self):
        self.assertAlmostEqual(FR.OPEN_Z - FR.BACK_Z, 0.35)
        self.assertGreater(FR.BACK_Z, 0.0)
        self.assertGreater(FR.SUBJECT_Z, FR.BACK_Z)
        self.assertLess(FR.SUBJECT_Z, FR.OPEN_Z)
        for s in self.sizes():
            x0, x1, y0, y1 = FR.outer_extent(s)
            piece, shades = FR.frame_piece(s)
            self.assertEqual(len(shades), len(piece.faces))
            pts = [p for f in piece.faces for p in f.pts]
            pts += [p for f in FR.backdrop_faces(s) + FR.lod1_faces(s) for p in f[0]]
            for x, y, z in pts:
                self.assertTrue(x0 - 1e-6 <= x <= x1 + 1e-6 and y0 - 1e-6 <= y <= y1 + 1e-6, (s, x, y))
                self.assertTrue(-1e-9 <= z <= FR.frame_depth() + 1e-6, (s, z))
            back = FR.backdrop_faces(s)[0][0]
            self.assertTrue(all(abs(p[2] - FR.BACK_Z) < 1e-9 for p in back))
            w, h = s
            self.assertAlmostEqual(max(p[0] for p in back) - min(p[0] for p in back), w)
            self.assertAlmostEqual(max(p[1] for p in back) - min(p[1] for p in back), h)

    def test_tags_uvs_and_determinism(self):
        for s in self.sizes():
            piece, shades = FR.frame_piece(s)
            again, shades2 = FR.frame_piece(s)
            self.assertEqual([(f.pts, f.tag, f.uv) for f in piece.faces], [(f.pts, f.tag, f.uv) for f in again.faces])
            self.assertEqual(shades, shades2)
            self.assertEqual({f.tag for f in piece.faces}, {FR.GILT, FR.OAK, FR.PLAQUE})
            for f in piece.faces:
                self.assertTrue(all(-1e-9 <= c <= 1 + 1e-9 for uv in f.uv for c in uv), f.tag)
            lo, hi = FR.tex_inner()
            for pts, uvs, sh in FR.backdrop_faces(s):
                self.assertTrue(all(lo - 1e-9 <= c <= hi + 1e-9 for uv in uvs for c in uv))
                self.assertTrue(all(0 < v <= 1 for v in sh))
            for pts, uvs, sh in FR.lod1_faces(s):
                self.assertTrue(all(0 <= c <= 1 for uv in uvs for c in uv))

    def test_faces_face_the_room(self):
        """The front strips face +z, the backdrop's back faces +z (toward the viewer)."""
        from castle_kit.pieces import geom

        s = (1.0, 1.3)
        back = FR.backdrop_faces(s)[0][0]
        self.assertGreater(geom.newell(back)[2], 0.0)
        piece, _ = FR.frame_piece(s)
        front = [f for f in piece.faces if f.tag == FR.GILT and geom.newell(f.pts)[2] > 1e-9]
        self.assertGreater(len(front), 20)

    def test_built_assets_match_the_manifest(self):
        man_path = os.path.join(ASSETS, "manifest.json")
        if not os.path.exists(man_path):
            self.skipTest("the portrait assets are not built (build_portraits.py)")
        man = load(man_path)
        self.assertEqual(set(man["frames"]), {PL.size_key(s) for s in self.sizes()})
        for key, f in man["frames"].items():
            self.assertEqual(f, f"portrait_frame_{key}.glb")
            self.assertTrue(os.path.exists(os.path.join(ASSETS, f)), f)
            lod0, lod1 = man["triangles"][key]
            self.assertLessEqual(lod0, FR.TRIS_LOD0)
            self.assertLessEqual(lod1, FR.TRIS_LOD1)
        self.assertEqual(man["backdrops"], [f"backdrop_{i}.png" for i in range(BD.COUNT)])
        self.assertEqual(man["depth_m"], FR.DEPTH)
        self.assertEqual(man["border_m"], FR.BORDER)
        self.assertLessEqual(man["draws"]["lod0"], 3)


class BackdropTests(unittest.TestCase):
    def test_palette_names_only(self):
        names = PA.palette()
        for n in BD.colours_used():
            self.assertIn(n, names)
        src = open(os.path.join(os.path.dirname(HERE), "npcs", "backdrops.py"), encoding="utf-8").read()
        code = src.split('"""', 2)[2]  # past the module docstring
        self.assertIsNone(re.search(r"#[0-9A-Fa-f]{6}", code), "no hex colours in the painter")
        for m in re.finditer(r'p\.(?:c|vgrad|mix|glow)\(\s*"([a-z_0-9]+)"', code):
            self.assertIn(m.group(1), BD.colours_used(), m.group(1))

    def test_deterministic_sized_and_ringed(self):
        a = BD.paint_backdrop(0)
        b = BD.paint_backdrop(0)
        self.assertEqual(a.shape, (BD.N, BD.N, 4))
        self.assertTrue((a == b).all())
        imgs = [a] + [BD.paint_backdrop(i) for i in range(1, BD.COUNT)]
        for i, im in enumerate(imgs):
            self.assertGreaterEqual(float(im[..., :3].min()), 0.0)
            self.assertLessEqual(float(im[..., :3].max()), 1.0)
            for j in range(i):
                self.assertFalse((im == imgs[j]).all(), (i, j))
            # The painted gilt ring (LOD1's moulding) is the same warm band on every backdrop.
            r = FR.RING_PX // 3
            band = im[BD.N // 2, r, :3]
            self.assertGreater(band[0], band[2])


class KeepOutTests(unittest.TestCase):
    """The decorative clusters keep grid.PORTRAIT_KEEP_OUT clear of every talking frame."""

    def test_clusters_clear_of_talking_frames(self):
        b = builder()
        placed = PL.resolve(b.layout)
        checked = 0
        for cell, cp in sorted(b.kit_plan.cells.items()):
            if not cp.portraits:
                continue
            ctx = R.context(b.layout, cp, b.kit_entries, b.open_edges)
            m5a = G.M5A_CELLS.get(cp.cell) == "corridors" and cp.shafts
            laid = (R.m5a_portrait_frames if m5a else R.portrait_frames)(ctx)
            for pc in cp.portraits:
                for p in placed:
                    if p.axis != pc.axis or p.sign != pc.sign or abs(p.c - pc.c) > 0.05:
                        continue
                    x0, x1, y0, y1 = FR.outer_extent(p.size)
                    ta, tb, ya, yb = p.t + x0, p.t + x1, p.pos[1] + y0, p.pos[1] + y1
                    for ft, fy, w, h, _shape, _canvas in laid[pc.id]:
                        gap = max(ta - (ft + w), ft - tb, ya - (fy + h), fy - yb)
                        self.assertGreaterEqual(gap, G.PORTRAIT_KEEP_OUT - 1e-6, (p.npc, pc.id))
                        checked += 1
        self.assertGreater(checked, 50)

    def test_keep_outs_follow_the_portraits(self):
        p = PL.resolve(layout())[0]
        ko = PL.keep_outs(layout(), p.axis, p.c, p.sign, G.PORTRAIT_KEEP_OUT)
        self.assertTrue(any(a <= p.t <= b and c <= p.pos[1] <= d for a, b, c, d in ko))
        self.assertEqual(PL.keep_outs(layout(), p.axis, p.c + 7.0, p.sign, 0.3), [])


class BuiltWallTests(unittest.TestCase):
    """Each frame hangs on solid wall (castle wall faces behind its whole
    back) and no built piece but the decorative clusters reaches into its
    volume (the frame's outer extent, from just off the wall to its front)."""

    def test_frames_on_solid_wall_clear_of_pieces(self):
        b = builder()
        lay = b.layout
        cache = {}

        def faces_of(cell):
            if cell not in cache:
                cache[cell] = b.build(cell).faces if cell in b.kit_plan.cells else []
            return cache[cell]

        for p in PL.resolve(lay):
            along_x = p.axis == "z"
            x0, x1, y0, y1 = FR.outer_extent(p.size)
            ta, tb, ya, yb = p.t + x0, p.t + x1, p.pos[1] + y0, p.pos[1] + y1
            zmax = FR.frame_depth()
            cells = {s.cell for s in lay.surfaces
                     if s.x0 - 4 < p.pos[0] < s.x1 + 4 and s.z0 - 4 < p.pos[2] < s.z1 + 4}
            samples = [(ta + (tb - ta) * (i + 0.5) / 6, ya + (yb - ya) * (j + 0.5) / 6) for i in range(6) for j in range(6)]
            covered = [False] * len(samples)
            for cell in sorted(cells):
                for f in faces_of(cell):
                    if f.lod == "lod1" or f.src.startswith("portraits:"):
                        continue
                    q = [((v[0] if along_x else v[2]), v[1], ((v[2] if along_x else v[0]) - p.c) * p.sign) for v in f.pts]
                    n = f.normal()
                    nd = (n[2] if along_x else n[0]) * p.sign
                    if all(abs(v[2]) < 0.04 for v in q) and nd > 0.9:
                        for k, (st, sy) in enumerate(samples):
                            if not covered[k] and _inside(q, st, sy):
                                covered[k] = True
                        continue
                    poly = q
                    for axis, val, ge in ((0, ta, True), (0, tb, False), (1, ya, True), (1, yb, False), (2, 0.03, True),
                                          (2, zmax, False)):
                        poly = _clip(poly, axis, val, ge)
                        if not poly:
                            break
                    self.assertFalse(poly and _area(poly) > 1e-6, (p.npc, cell, f.src))
            self.assertTrue(all(covered), (p.npc, sum(covered), len(covered)))


def _inside(q, x, y) -> bool:
    inside = False
    m = len(q)
    for k in range(m):
        a, b = q[k], q[(k + 1) % m]
        if (a[1] > y) != (b[1] > y):
            if a[0] + (y - a[1]) * (b[0] - a[0]) / (b[1] - a[1]) > x:
                inside = not inside
    return inside


def _clip(poly, axis, val, keep_ge):
    out = []
    m = len(poly)
    for k in range(m):
        a, b = poly[k], poly[(k + 1) % m]
        da = (a[axis] - val) if keep_ge else (val - a[axis])
        db = (b[axis] - val) if keep_ge else (val - b[axis])
        if da >= 0:
            out.append(a)
        if (da < 0) != (db < 0):
            t = da / (da - db)
            out.append(tuple(a[j] + (b[j] - a[j]) * t for j in range(3)))
    return out


def _area(poly) -> float:
    from castle_kit.pieces import geom

    return geom.norm(geom.newell(poly)) / 2.0 if len(poly) >= 3 else 0.0


class LookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roster = load(os.path.join(NPCS_DIR, "roster.json"))["npcs"]
        cls.looks = load(os.path.join(NPCS_DIR, "looks.json"))["looks"]
        cls.creator = load(os.path.join(REPO, "data", "characters", "creator.json"))
        cls.catalog = {i["id"]: i for i in load(os.path.join(REPO, "data", "items", "catalog.json"))["items"]}
        cls.palette = load(os.path.join(REPO, "data", "items", "palette.json"))

    def test_every_npc_has_a_wearable_look(self):
        self.assertEqual(set(self.looks), {n["id"] for n in self.roster})
        bodies = self.creator["body_types"]
        styles = {h["id"]: h for h in self.creator["hair_styles"] if not h.get("retired")}
        for npc, a in self.looks.items():
            body = bodies[a["body_type"]]
            self.assertEqual(styles[a["hair_style"]]["body"], body["id"], npc)
            self.assertLess(a["skin_tone"], len(self.creator["skin_tones"]))
            self.assertLess(a["hair_colour"], len(self.creator["hair_colours"]))
            self.assertLess(a["eye_colour"], len(self.creator["eye_colours"]))
            self.assertEqual(len(a["face"]), 5)
            self.assertTrue(0 <= a["height"] <= 255)
            self.assertIsNotNone(a["outfit"]["body"], npc)
            for slot, w in a["outfit"].items():
                if w is None:
                    continue
                item = self.catalog[w["item_def"]]
                self.assertEqual((item["kind"], item["slot"]), ("clothing", slot), npc)
                self.assertIn(item.get("wear"), (None, body["wear"]), npc)
                self.assertEqual(len(w["dyes"]), 3)
                self.assertTrue(all(0 <= d < len(self.palette["colours"]) for d in w["dyes"]), npc)
                self.assertEqual(w["finish"], "none")

    def test_students_wear_their_houses_robe_others_do_not(self):
        robe_of = {}
        for i in self.catalog.values():
            for t in i.get("tags", []):
                if t.startswith("house:") and i.get("slot") == "body":
                    robe_of[t.split(":", 1)[1]] = i["id"]
        self.assertEqual(len(robe_of), 4)
        for n in self.roster:
            body = self.looks[n["id"]]["outfit"]["body"]["item_def"]
            if n["kind"] == "student":
                self.assertEqual(body, robe_of[n["house"]], n["id"])
                dyes = self.looks[n["id"]]["outfit"]["body"]["dyes"]
                hc = self.palette["houses"][n["house"]]
                self.assertEqual(dyes[1:], [hc["primary"], hc["secondary"]], n["id"])
            else:
                self.assertNotIn(body, robe_of.values(), n["id"])

    def test_looks_are_varied(self):
        def key(a):
            return (a["body_type"], a["hair_style"], a["hair_colour"], a["outfit"]["body"]["item_def"],
                    tuple(a["outfit"]["body"]["dyes"]))

        keys = [key(a) for a in self.looks.values()]
        self.assertEqual(len(keys), len(set(keys)))
        for kind in ("portrait", "student"):
            ids = [n["id"] for n in self.roster if n["kind"] == kind]
            self.assertGreaterEqual(len({self.looks[i]["skin_tone"] for i in ids}), 6, kind)
            self.assertGreaterEqual(len({self.looks[i]["hair_style"] for i in ids}), 8, kind)
            self.assertEqual({self.looks[i]["body_type"] for i in ids}, {0, 1}, kind)


class NamesTests(unittest.TestCase):
    """No IP name in the NPC code or asset names (they live in data)."""

    def test_no_house_names_in_code(self):
        houses = set(load(os.path.join(REPO, "data", "items", "palette.json"))["houses"])
        d = os.path.join(os.path.dirname(HERE), "npcs")
        for f in sorted(os.listdir(d)):
            if f.endswith(".py"):
                src = open(os.path.join(d, f), encoding="utf-8").read().lower()
                for h in houses:
                    self.assertNotIn(h, src, f)


if __name__ == "__main__":
    unittest.main()
