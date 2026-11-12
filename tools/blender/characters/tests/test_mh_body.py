"""The MakeHuman body source (charlib/mh_body.py, mh_paint.py, mh_source.py).

Skips when MPFB is not installed in this Blender's user resources (run with
BLENDER_USER_RESOURCES=<worktree>/.blender-user). Builds body_b once (about
a minute and a half).
"""

import unittest

import numpy as np

from charlib import atlas as A
from charlib import body as BD
from charlib import body_mesh as BM
from charlib import skeleton as K
from charlib import weights as W
from charlib.proportions import BODIES, BUILDS

try:
    from charlib import mh_source as MS

    MS.enable()
    HAVE_MPFB = True
except Exception:  # noqa: BLE001 - any failure to load the add-on means "skip"
    HAVE_MPFB = False


class CutTests(unittest.TestCase):
    """Pure geometry: splitting polygons along a field (no MPFB needed)."""

    def grid(self, n=6):
        xs = np.linspace(0.0, 1.0, n)
        pts = np.array([[x, y, 0.0] for y in xs for x in xs])
        faces = [(j * n + i, j * n + i + 1, (j + 1) * n + i + 1, (j + 1) * n + i) for j in range(n - 1) for i in range(n - 1)]
        return pts, faces

    def test_cut_makes_a_crisp_border_and_keeps_shapes(self):
        from charlib import mh_paint as MP

        pts, faces = self.grid()
        key = pts + np.array([0.0, 0.0, 1.0])
        w = [{"DEF-spine": 1.0} if p[0] < 0.5 else {"DEF-spine.001": 1.0} for p in pts]
        cm = MP.CutMesh({"base": pts, "lift": key}, w, faces, {"tag": list(range(len(faces)))})
        field = lambda P: P[:, 0] + 0.3 * P[:, 1] - 0.55  # noqa: E731
        added = cm.cut(field, np.ones(len(faces), dtype=bool))
        self.assertGreater(added, 0)
        f = field(cm.base)
        for face in cm.faces:
            v = f[list(face)]
            self.assertFalse((v > 1e-9).any() and (v < -1e-9).any(), "a face still straddles the border")
        np.testing.assert_allclose(cm.shapes["lift"] - cm.base, np.tile([0.0, 0.0, 1.0], (len(cm.base), 1)), atol=1e-12)
        for wt in cm.weights:
            self.assertAlmostEqual(sum(wt.values()), 1.0, places=9)
        area = sum(0.5 * np.linalg.norm(np.cross(cm.base[list(fc)][1] - cm.base[list(fc)][0],
                                                 cm.base[list(fc)][-1] - cm.base[list(fc)][0])) for fc in cm.faces)
        self.assertGreater(area, 0.0)


class AnimeFaceTests(unittest.TestCase):
    """The painted anime face's 2D design (charlib/mh_face.py, no MPFB needed)."""

    def test_polygon_distance_sign(self):
        from charlib import mh_face as MF

        sq = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
        d = MF.poly_sdf(np.array([[0.5, 0.5], [2.0, 0.5], [0.5, 0.9]]), sq)
        self.assertLess(d[0], 0)
        self.assertAlmostEqual(d[1], 1.0)
        self.assertAlmostEqual(d[2], -0.1)

    def test_ear_clipping_covers_concave_polygons(self):
        from charlib import mh_face as MF

        # An L shape with a collinear (inserted) vertex on one edge.
        Q = np.array([[0, 0], [1, 0], [2, 0], [2, 1], [1, 1], [1, 2], [0, 2]], dtype=float)
        tris = MF._earclip(Q)
        self.assertEqual(len(tris), len(Q) - 2)
        area = sum(abs(np.cross(Q[b] - Q[a], Q[c] - Q[a])) / 2 for a, b, c in tris)
        self.assertAlmostEqual(area, 3.0)

    def test_eye_design_is_nested_and_adult(self):
        from charlib import mh_face as MF

        for body in ("body_a", "body_b"):
            st = MF.STYLE[body]
            P = MF.eye_polys(st)
            inside = lambda name, pts: MF.poly_sdf(pts, P[name]) < 0  # noqa: E731
            # The opening sits inside the lash outline, the pupil inside the
            # iris, the main catch light over the iris.
            c = P["eye_open"].mean(axis=0)
            self.assertTrue(inside("eye_all", c + (P["eye_open"] - c) * 0.9).all(), body)
            self.assertTrue(inside("iris", P["pupil"]).all(), body)
            self.assertTrue(inside("iris", P["hl1"].mean(axis=0, keepdims=True)).all(), body)
            # Larger than realistic (a real eye opening is about 30 x 10 mm)
            # but not childlike: wider than tall by a clear margin.
            w = np.ptp(P["eye_open"][:, 0])
            h = np.ptp(P["eye_open"][:, 1])
            self.assertGreater(w, 0.030, body)
            self.assertLess(w, 0.040, body)
            self.assertGreater(w / h, 1.7, body)
        # The male eye is narrower and its brow heavier than the female's.
        self.assertLess(MF.STYLE["body_a"]["eye_up"], MF.STYLE["body_b"]["eye_up"])
        self.assertGreater(MF.STYLE["body_a"]["brow_th"], MF.STYLE["body_b"]["brow_th"])


class MappingTests(unittest.TestCase):
    """Parameter mappings (no MPFB needed)."""

    def test_age_mapping_matches_makehuman(self):
        from charlib import mh_body as MB

        # MakeHuman/MPFB: 0 = 1 year, 0.1875 = 11, 0.5 = 25, 1 = 90.
        self.assertAlmostEqual(MB.age_macro(1), 0.0)
        self.assertAlmostEqual(MB.age_macro(11), 0.1875)
        self.assertAlmostEqual(MB.age_macro(25), 0.5)
        self.assertAlmostEqual(MB.age_macro(90), 1.0)
        for body in ("body_a", "body_b"):
            self.assertAlmostEqual(MB.MH_MACROS[body]["age"], MB.age_macro(18.0))
        self.assertEqual(MB.MH_MACROS["body_a"]["gender"], 1.0)  # male
        self.assertEqual(MB.MH_MACROS["body_b"]["gender"], 0.0)  # female

    def test_twist_segments_follow_their_limb_in_the_fit(self):
        """Regression: DEF-forearm.L.001 etc. used to map to nothing, so the
        lower half of each limb was posed by the torso transform."""
        from charlib import mh_body as MB

        for limb in ("upper_arm", "forearm", "thigh", "shin"):
            for s in ("L", "R"):
                self.assertEqual(MB._weight_target(f"DEF-{limb}.{s}.001"), f"{limb}.{s}")
                self.assertEqual(MB._skin_target(f"DEF-{limb}.{s}.001"), [(f"DEF-{limb}.{s}.001", 1.0)])
        self.assertEqual(MB._weight_target("brow.B.L.001"), "head")
        # Face groups with a numbered side suffix must not become DEF bones
        # that do not exist (those vertices exported with no weights).
        for g in ("brow.B.L.001", "lid.T.R.001", "ear.L.001", "cheek.B.R.001", "jaw.L.001", "lip.T.L.001"):
            self.assertEqual(MB._skin_target(g), [("DEF-spine.006", 1.0)], g)
        self.assertEqual(MB._weight_target("toe2-3.R"), "toe.R")


@unittest.skipUnless(HAVE_MPFB, "MPFB (MakeHuman) is not installed in this Blender's user resources")
class MakeHumanBodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from charlib import mh_body as MB

        cls.MB = MB
        cls.body = BODIES["body_b"]
        cls.lay = BD.BodyLayout(cls.body)
        cls.res = MB.build(cls.lay, list(BM.SHAPE_KEYS))

    def test_fit_puts_makehuman_joints_on_our_skeleton(self):
        MB = self.MB
        b = self.body
        rig = MS.rig_definition()
        groups = MS.vertex_groups()
        co0 = MS.human(MS.default_macro(**MB.MH_MACROS[b.id]))
        fb = MB.fit_bones(b, co0, rig, groups)
        names = [x.name for x in fb]
        Wm = MB.mapped_weights(len(co0), names)
        # The joint cubes carry no weights: give each cube vertex the weights
        # of the nearest skin vertex, so they move like the flesh around them.
        cubes = np.concatenate([groups[g] for g in ("joint-l-knee", "joint-l-ankle", "joint-l-elbow", "joint-l-hand",
                                                    "joint-l-shoulder", "joint-l-upper-leg")])
        skin = co0[: MS.BODY_VERTS]
        for v in cubes:
            Wm[v] = Wm[int(np.argmin(np.linalg.norm(skin - co0[v], axis=1)))]
        fit = MB.Fit(b, fb, Wm)
        fitted = co0.copy()
        fitted[cubes] = fit.apply(co0, cubes)
        J = K.joint_map(b)
        for cube, joint in (("joint-l-knee", J["shin.L"].head), ("joint-l-ankle", J["foot.L"].head),
                            ("joint-l-elbow", J["forearm.L"].head), ("joint-l-hand", J["hand.L"].head),
                            ("joint-l-shoulder", J["upper_arm.L"].head), ("joint-l-upper-leg", J["thigh.L"].head)):
            d = np.linalg.norm(fitted[groups[cube]].mean(axis=0) - joint)
            self.assertLess(d, 0.012, f"{cube} is {d * 1000:.0f} mm off our joint")

    def test_style_targets_exist(self):
        import os

        for body, spec in self.MB.MH_STYLE.items():
            for rel in spec:
                self.assertTrue(os.path.exists(MS.target_path(rel)), f"{body}: {rel}")

    def test_every_limb_vertex_follows_a_limb_bone(self):
        MB = self.MB
        rig = MS.rig_definition()
        groups = MS.vertex_groups()
        co0 = MS.human(MS.default_macro(**MB.MH_MACROS["body_b"]))
        names = [x.name for x in MB.fit_bones(self.body, co0, rig, groups)]
        Wm = MB.mapped_weights(len(co0), names)
        torso = names.index("torso")
        for group, entries in MS.rig_weights().items():
            if group.endswith(("forearm.L.001", "shin.L.001")):
                ids = [vi for vi, w in entries if w > 0.9 and vi < MS.BODY_VERTS]
                self.assertTrue(ids, group)
                self.assertLess(float(Wm[ids, torso].max()), 0.05, group)

    def test_visible_mesh_contract(self):
        md = self.res.visible
        self.assertEqual(set(md.region), set(BD.REGIONS))
        self.assertTrue(set(md.swatch) <= set(A.SWATCHES), set(md.swatch) - set(A.SWATCHES))
        # The face's features are painted in the face texture (character look);
        # only the cut areas the expressions need keep a swatch of their own.
        for s in ("underwear", "underwear_trim", "lash", "nails", "sclera", "mouth_line"):
            self.assertIn(s, md.swatch)
        head = [s for s, r in zip(md.swatch, md.region) if r == "head"]
        self.assertTrue(set(head) <= {"skin", "sclera", "lash", "mouth_line"}, set(head))
        head_masks = {m for m, r in zip(md.mask, md.region) if r == "head"}
        self.assertEqual(head_masks, {(1.0, 0.0, 0.0)})  # the skin tone; eyes and brows tint in the texture
        if self.MB.HEAD != "anime":
            self.assertIn("mouth", md.swatch)  # MakeHuman's own mouth cavity (the anime head paints its open mouth)
        self.assertNotIn("lips", md.swatch)  # anime: lips are not coloured, the mouth is a painted line
        names = set()
        for w in md.weights:
            self.assertTrue(w, "unweighted vertex")
            self.assertLessEqual(len(w), W.MAX_INFLUENCES)
            self.assertAlmostEqual(sum(w.values()), 1.0, places=5)
            names |= set(w)
        from test_rig import spec_bones

        self.assertTrue(names <= spec_bones(), sorted(names - spec_bones()))
        self.assertNotIn("DEF-jaw", names)  # the face uses shape keys (base-body-spec.md section 1)
        self.assertTrue(set(BM.SHAPE_KEYS) <= set(md.keys), set(BM.SHAPE_KEYS) - set(md.keys))
        for k, v in md.keys.items():
            self.assertEqual(v.shape, md.verts.shape, k)
        self.assertEqual(len(md.corner_uv), 0)  # no eyeballs: the eyes are painted regions of the skin
        self.assertGreater(md.tri_count(), self.MB.LOD0_BUDGET)  # decimated to the budget at export

    def test_anime_head_is_welded_on(self):
        """The anime head shares the seam loop's vertices: the visible body
        has no open edge (the ears are separate closed shells)."""
        if self.MB.HEAD != "anime":
            self.skipTest("MakeHuman head")
        md = self.res.visible
        count = {}
        for f in md.faces:
            for i in range(len(f)):
                e = (min(f[i], f[i - 1]), max(f[i], f[i - 1]))
                count[e] = count.get(e, 0) + 1
        open_edges = [e for e, n in count.items() if n == 1]
        self.assertEqual(open_edges, [], f"{len(open_edges)} open edges")
        self.assertEqual(self.res.head, "anime")
        # The head is light: a clean grid plus the painted feature outlines.
        head_tris = sum(len(f) - 2 for f, r in zip(md.faces, md.region) if r in ("head", "ears"))
        self.assertLess(head_tris, 4200)

    def test_face_and_expression_keys_only_move_the_head(self):
        md = self.res.visible
        for k in ("face_jaw_wide", "face_nose_large", "smile", "blink_l", "mouth_open"):
            moved = np.linalg.norm(md.keys[k] - md.verts, axis=1) > 1e-4
            self.assertTrue(moved.any(), k)
            self.assertGreater(md.verts[moved, 2].min(), self.body.neck_base_z - 0.03, k)

    def test_blink_closes_the_painted_eye(self):
        md = self.res.visible

        def area(P, swatch):
            a = 0.0
            for f, s in zip(md.faces, md.swatch):
                if s != swatch or md.verts[f[0], 0] <= 0:  # left eye (+x)
                    continue
                p = P[list(f)]
                for i in range(1, len(f) - 1):
                    a += 0.5 * np.linalg.norm(np.cross(p[i] - p[0], p[i + 1] - p[0]))
            return a

        for s in ("sclera",):  # the eye opening (iris and highlights are texture now)
            open_a = area(md.verts, s)
            self.assertGreater(open_a, 1e-5, s)
            self.assertLess(area(md.keys["blink_l"], s), 0.45 * open_a, s)  # squashed to a sliver band on the curved lid
            self.assertAlmostEqual(area(md.keys["blink_r"], s), open_a, delta=0.05 * open_a)

    def test_builds_keep_the_skeleton(self):
        md = self.res.visible
        for k in BUILDS:
            d = np.linalg.norm(md.keys[k] - md.verts, axis=1)
            self.assertGreater(d.max(), 0.005, k)
            self.assertLess(d.max(), 0.09, k)

    def test_regions_are_crisp_along_layout_borders(self):
        """Every face is on one side of each limb border plane it is near."""
        from charlib import mh_paint as MP

        md = self.res.visible
        bd = MP.Borders(self.lay)
        for side in (1, -1):
            f = bd.plane(*bd.arm[side]["elbow"])
            for face, r in zip(md.faces, md.region):
                if r not in ("upper_arms", "forearms"):
                    continue
                v = f(md.verts[list(face)])
                c = md.verts[list(face)].mean(axis=0)
                if c[0] * side < 0.2 or np.abs(v).max() > 0.05:
                    continue
                self.assertFalse((v > 2e-3).any() and (v < -2e-3).any(), "a face straddles the elbow border")

    def test_cage_is_our_layout_on_the_makehuman_surface(self):
        cage = self.res.cage
        ref = BM.body_meshdata(self.lay, with_keys=False)
        self.assertEqual(len(cage.verts), len(ref.verts))
        self.assertEqual(cage.faces, ref.faces)
        self.assertEqual(cage.tags, ref.tags)
        self.assertEqual(set(cage.keys), set(BUILDS))
        from mathutils import Vector
        from mathutils.bvhtree import BVHTree

        md = self.res.visible
        tris = [(f[0], f[i], f[i + 1]) for f in md.faces for i in range(1, len(f) - 1)]
        bvh = BVHTree.FromPolygons([tuple(v) for v in md.verts], tris, all_triangles=True)
        trunk = [i for i, r in enumerate(ref.region) if r in ("torso", "hips", "thighs", "calves", "upper_arms", "forearms",
                                                                   "neck")]
        vids = sorted({v for i in trunk for v in cage.faces[i]})
        d = np.array([bvh.find_nearest(Vector(cage.verts[v]))[3] for v in vids])
        self.assertLess(float(np.median(d)), 0.002)
        self.assertLess(float(np.percentile(d, 98)), 0.01)

    def test_spec_measures(self):
        m = self.res.report["measures"]
        b = self.body
        self.assertAlmostEqual(m["height_m"], b.height, delta=0.015)
        self.assertAlmostEqual(m["head_m"], 0.235 * b.head_scale, delta=0.01)


if __name__ == "__main__":
    unittest.main()
