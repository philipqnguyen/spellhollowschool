"""The face texture pair and the face-shadow map (docs/art/character-look.md
section 3; charlib/face_paint.py, charlib/face_shadow.py): the projection,
the paint and the threshold design on a synthetic frame (no mesh needed),
then the exported textures, head UVs and threshold maps when the assets
root has them."""

import json
import os
import struct
import unittest

import numpy as np

from charlib import face_paint as FP
from charlib import face_shadow as FS
from charlib import mh_face as MF
from test_sculpt_face import frame

ASSETS = os.environ.get("SHS_CHARACTER_ASSETS", os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "client", "public", "assets"))
MANIFEST = os.path.join(ASSETS, "characters", "manifest.json")


def fmap_of(fr):
    return FP.FaceMap.for_frame(fr, 1.40, 1.64)


class FaceMapTests(unittest.TestCase):
    def test_symmetric_and_invertible(self):
        fm = fmap_of(frame())
        a = np.linspace(-170, 170, 69)
        u = fm.u_of_angle(a)
        np.testing.assert_allclose(u + fm.u_of_angle(-a), 1.0, atol=1e-12)
        self.assertTrue((np.diff(u) >= 0).all())
        self.assertEqual(float(u.min()), 0.0)
        self.assertEqual(float(u.max()), 1.0)
        inner = np.abs(a) < FP.A_MAX - 1
        np.testing.assert_allclose(fm.angle_of_u(u[inner]), a[inner], atol=1e-6)
        z = np.linspace(fm.z0, fm.z1, 50)
        np.testing.assert_allclose(fm.z_of_v(fm.v_of_z(z)), z, atol=2e-5)

    def test_face_front_gets_most_of_the_texture(self):
        fr = frame()
        fm = fmap_of(fr)
        # At least 700 texels (of 1024) across the face front (+-60 degrees) ...
        self.assertGreater((fm.u_of_angle(60.0) - fm.u_of_angle(-60.0)) * FP.COLOUR_SIZE, 700)
        # ... and an eye at least 110 texels wide.
        e = MF.EyeShape(fr.st)
        a0 = np.degrees((fr.eye[0] + e.s_in) / fr.R0)
        a1 = np.degrees((fr.eye[0] + e.s_out) / fr.R0)
        self.assertGreater((fm.u_of_angle(a1) - fm.u_of_angle(a0)) * FP.COLOUR_SIZE, 110)

    def test_back_seam_lands_on_one_edge(self):
        fm = fmap_of(frame())
        P = np.array([[0.01, 0.20, 1.5], [-0.01, 0.20, 1.5], [0.0, 0.21, 1.52], [0.03, -0.05, 1.5], [0.04, -0.05, 1.5], [0.03, -0.05, 1.52]])
        uv = fm.corner_uvs(P, [np.array([0, 1, 2]), np.array([3, 4, 5])])
        back = uv[0][:, 0]
        self.assertTrue(np.all(back == back[0]) and back[0] in (0.0, 1.0), back)
        self.assertTrue(0.5 < uv[1][:, 0].min() < 0.8)


class PaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fr = frame()
        cls.fm = fmap_of(cls.fr)
        cls.cv = FP.paint(cls.fr, cls.fm, "body_b", size=512)

    def texel(self, qu, qv):
        """Row, column of the canvas texel nearest face coordinates (qu, qv)."""
        cv = self.cv
        c = int(np.argmin(np.abs(cv.qu[0] - qu)))
        r = int(np.argmin(np.abs(cv.qv[:, 0] - qv)))
        return r, c

    def test_features_sit_on_the_eye_and_brow(self):
        fr, cv = self.fr, self.cv
        e = MF.EyeShape(fr.st)
        for side in (1, -1):
            ic = e.iris_centre()
            r, c = self.texel(side * (fr.eye[0] + ic[0]), fr.eye[1] + ic[1] - 0.6 * fr.st["iris_ry"])
            self.assertGreater(cv.mr[r, c], 0.7)  # the iris takes the eye colour
            self.assertEqual(cv.skin[r, c], 0.0)
            self.assertGreater(cv.ma[r, c], 0.9)  # flat
            # The brow takes the hair colour.
            bp = fr.polys["brow_L" if side > 0 else "brow_R"]
            mid = 0.5 * (bp[len(bp) // 4] + bp[-(len(bp) // 4) - 1])  # on the stroke's centre line
            r, c = self.texel(float(mid[0]), float(mid[1]))
            self.assertGreater(cv.mg[r, c], 0.5)
        # The cheek is skin.
        r, c = self.texel(0.035, fr.mouth[1] + 0.015)
        self.assertEqual(cv.skin[r, c], 1.0)
        self.assertEqual(cv.mr[r, c] + cv.mg[r, c] + cv.ma[r, c], 0.0)

    def test_scalp_takes_the_hair_colour(self):
        fr, cv = self.fr, self.cv
        H = fr.hair_z - fr.eye[1]
        at = lambda deg, dz: self.texel(fr.R0 * np.radians(deg), fr.eye[1] + dz * H)
        # Above the hairline (crown, temple above the eye, behind the ear): hair, not skin, shaded.
        for deg, dz in ((0.0, 1.25), (70.0, 0.6), (130.0, 0.0)):
            r, c = at(deg, dz)
            self.assertGreater(cv.mg[r, c], 0.9, (deg, dz))
            self.assertLess(cv.skin[r, c], 0.1, (deg, dz))
            self.assertEqual(cv.ma[r, c], 0.0)
        # The forehead under the hairline and the ear stay skin.
        for deg, dz in ((0.0, 0.8), (96.0, 0.3)):
            r, c = at(deg, dz)
            self.assertEqual(cv.mg[r, c], 0.0, (deg, dz))
            self.assertEqual(cv.skin[r, c], 1.0, (deg, dz))

    def test_paint_is_symmetric(self):
        cv = self.cv
        for a in (cv.mr, cv.mg, cv.ma, cv.skin, cv.glow):
            self.assertLess(float(np.abs(a - a[:, ::-1]).mean()), 0.01)

    def test_skin_lines_mark_red_and_tints_keep_it(self):
        """Look pass 2 (character-look.md section 3): the mouth line, the nose shade
        and the lid crease are multipliers with R < 1 on skin (the client
        deepens them on dark skin from R); blush and lip tint keep R = 1."""
        fr, cv = self.fr, self.cv
        # The mouth line's centre (the painted one): a clear line on skin.
        mp = FP.mouth_poly(fr, "body_b")
        r, c = self.texel(0.0, float(mp[:, 1].mean()))
        self.assertLess(cv.rgb[r, c, 0], 0.8)
        self.assertEqual(cv.skin[r, c], 1.0)
        # Under the nose tip: the mark over the soft shade, R below 1.
        r, c = self.texel(*FP.nose_mark_centre(fr))
        self.assertLess(cv.rgb[r, c, 0], 0.97)
        # The blush (under the eye, toward the outer corner) tints G and B only.
        e = MF.EyeShape(fr.st)
        bu = fr.eye[0] + 0.06 * fr.st["eye_w"]
        bv = fr.eye[1] + float(e.lower(np.array([0.0]))[0]) - 0.0068 * fr.k
        r, c = self.texel(bu, bv)
        self.assertLess(cv.rgb[r, c, 1], 0.97)
        self.assertEqual(cv.rgb[r, c, 0], 1.0)
        # Skin paint only ever multiplies, and most of the skin is plain.
        skin = cv.skin > 0.99
        self.assertTrue((cv.rgb[skin] <= 1.0).all())
        self.assertGreater(float((cv.rgb[skin][:, 0] >= 1.0).mean()), 0.9)

    def test_iris_glows_low_and_catchlights_most(self):
        """The glow: on the iris (strongest low down) and the catchlights,
        nowhere that is not flat paint."""
        fr, cv = self.fr, self.cv
        e = MF.EyeShape(fr.st)
        ic = e.iris_centre()
        rx, ry = fr.st["iris_rx"], fr.st["iris_ry"]
        r, c = self.texel(fr.eye[0] + ic[0] - 0.2 * rx, fr.eye[1] + ic[1] - 0.65 * ry)
        low = cv.glow[r, c]
        r, c = self.texel(fr.eye[0] + ic[0] - 0.2 * rx, fr.eye[1] + ic[1] + 0.6 * ry)
        high = cv.glow[r, c]
        self.assertGreater(low, 0.8)
        self.assertGreater(low, high + 0.3)
        self.assertGreater(high, 0.2)
        # The larger catchlight (upper outer) glows fully and stays white.
        r, c = self.texel(fr.eye[0] + ic[0] + 0.30 * rx, fr.eye[1] + ic[1] + 0.40 * ry)
        self.assertGreater(cv.glow[r, c], 0.95)
        self.assertGreater(cv.rgb[r, c].min(), 0.95)
        self.assertLess(cv.mr[r, c], 0.05)
        # Never on skin, scalp or brows: only inside flat paint (the mask
        # stores it as flat x glow, so an anti-aliased edge of the eye fades it).
        self.assertEqual(float(cv.glow[cv.ma == 0.0].max()), 0.0)
        self.assertLess(float((cv.ma * cv.glow)[cv.ma < 0.5].max()), 0.5)

    def test_mask_alpha_encodes_flat_then_glow(self):
        cv = self.cv
        a = FP.mask_image(cv, None)[..., 3]
        self.assertEqual(round(FP.FLAT_LEVEL * 255), 192)  # exact in 8 bits
        self.assertLessEqual(float(a.max()), 1.0 + 1e-9)
        self.assertGreater(float(a.max()), FP.FLAT_LEVEL + 0.15)  # the glow is there
        # The client's decoding (toon/material.ts faceFlatGlow) gives back the flat weight.
        flat = np.minimum(1.0, a / (FP.FLAT_LEVEL - 0.002))
        want = np.minimum(1.0, FP.downsample(cv.ma, cv.size // FP.MASK_SIZE) * FP.FLAT_LEVEL / (FP.FLAT_LEVEL - 0.002))
        # Exact except where a texel averages glowing and unpainted texels (a
        # catchlight against the lid): a little more flat there, never less.
        err = flat - want
        self.assertLess(float((np.abs(err) > 0.02).mean()), 0.001)
        self.assertGreater(float(err.min()), -0.02)
        self.assertLess(float(err.max()), 0.2)

    def test_colours_stay_choosable(self):
        cv = self.cv
        tinted = (cv.mr > 0.5) | (cv.mg > 0.5)
        # The tinted paint is greyscale (the creator colour gives the hue).
        rgb = cv.rgb[tinted & (cv.ma > 0.5)]
        self.assertLess(float(np.abs(rgb - rgb.mean(axis=1, keepdims=True)).max()), 0.12)
        self.assertTrue((cv.rgb >= 0).all() and (cv.rgb <= 1).all())


class MouthNoseTests(unittest.TestCase):
    """User, 2026-11-01 (paint only): both mouths a little wider, the male's
    lower, and a more obvious nose (a crisp mark under the tip; the bridge's
    shadow stroke is in the face-shadow map, ShadowDesignTests)."""

    @classmethod
    def setUpClass(cls):
        cls.canvases = {}
        for body in ("body_a", "body_b"):
            fr = frame(body)
            cls.canvases[body] = (fr, FP.paint(fr, fmap_of(fr), body, size=1024))

    def column(self, body):
        """The canvas's middle column (u = 0): its v and the paint's R."""
        fr, cv = self.canvases[body]
        c = int(np.argmin(np.abs(cv.qu[0])))
        return cv.qv[:, c], cv.rgb[:, c, 0], c

    def test_mouths_wider_than_the_cut_line(self):
        for body in ("body_a", "body_b"):
            fr, cv = self.canvases[body]
            cut = fr.world_polys()["mouth"]
            painted = FP.mouth_poly(fr, body)
            ratio = np.ptp(painted[:, 0]) / np.ptp(cut[:, 0])
            self.assertAlmostEqual(ratio, FP.MOUTH_PAINT[body]["w"], places=9)
            self.assertGreater(ratio, 1.05, body)  # a little wider ...
            self.assertLess(ratio, 1.25, body)  # ... not a grin
            # The paint reaches past the cut line's ends at the mouth's height.
            v = float(painted[:, 1].mean())
            r = int(np.argmin(np.abs(cv.qv[:, 0] - v)))
            dark = np.nonzero(cv.rgb[r, :, 0] < 0.9)[0]
            span = cv.qu[r, dark.max()] - cv.qu[r, dark.min()]
            self.assertGreater(span, 0.85 * np.ptp(cut[:, 0]) * FP.MOUTH_PAINT[body]["w"], body)

    def test_male_mouth_lower_female_kept(self):
        """The male's mouth line moves down (his was much closer to the nose
        than hers), hers stays where the style puts it."""
        self.assertLess(FP.MOUTH_PAINT["body_a"]["dv"], -0.006)
        self.assertGreater(FP.MOUTH_PAINT["body_a"]["dv"], -0.013)
        self.assertEqual(FP.MOUTH_PAINT["body_b"]["dv"], 0.0)
        for body in ("body_a", "body_b"):
            fr, _ = self.canvases[body]
            qv, R, _ = self.column(body)
            near = np.abs(qv - fr.mouth[1]) < 0.02
            v_line = float(qv[near][np.argmin(R[near])])  # the darkest paint near the mouth: the line
            want = float(FP.mouth_poly(fr, body)[:, 1].mean())
            self.assertLess(abs(v_line - want), 0.0006, body)

    def test_nose_mark_crisp_and_darker_than_the_shade(self):
        for body in ("body_a", "body_b"):
            fr, cv = self.canvases[body]
            km = fr.st["mouth_k"]
            qv, R, _ = self.column(body)
            mu, mv = FP.nose_mark_centre(fr)
            r = int(np.argmin(np.abs(qv - mv)))
            self.assertLess(R[r], 0.8, body)  # a clear mark (the old smudge: 0.92 at best)
            self.assertGreater(R[r], float(FP.PAINT["mouth"][0]) - 1e-6, body)  # never darker than the mouth line
            # Crisp: a mark's thickness above or below, the skin is nearly plain.
            for dv in (1.5, -1.5):
                rr = int(np.argmin(np.abs(qv - (mv + dv * FP.NOSE_MARK["th"] * FP.LINE_THICK * km))))
                self.assertGreater(R[rr], 0.9, (body, dv))
            # Skin weight and the flat mask are untouched (it is lit like skin).
            self.assertEqual(cv.skin[r, cv.size // 2], 1.0)
            self.assertEqual(cv.ma[r, cv.size // 2], 0.0)

    def test_new_paint_rows_are_lines(self):
        """The line rows of every option keep the line rules the client
        relies on (toon/material.ts faceLinePaint): R < 1 and R the lightest
        ratio (warm, never grey or cool); the nose mark never darker than the
        mouth line."""
        for opt, o in FP.LINE_OPTIONS.items():
            for name in ("mouth", "nose_tip"):
                c = o[name]
                self.assertLess(c[0], 1.0, (opt, name))
                self.assertGreater(c[0], c[1], (opt, name))
                self.assertGreater(c[1], c[2], (opt, name))
            self.assertGreaterEqual(o["nose_tip"][0], o["mouth"][0], opt)

    def test_line_options(self):
        """The darker options (user, 2026-11-01, choice pending): A dark
        brown-black, B near-black and a touch thicker; the built one is a
        named option and PAINT carries its rows."""
        L = FP.LINE_OPTIONS
        self.assertEqual(L["current"]["mouth"], (0.58, 0.40, 0.38))
        self.assertAlmostEqual(L["A"]["mouth"][0], 0.30, delta=0.03)
        self.assertAlmostEqual(L["B"]["mouth"][0], 0.15, delta=0.03)
        self.assertEqual(L["A"]["thick"], 1.0)
        self.assertAlmostEqual(L["B"]["thick"], 1.3)
        self.assertIn(FP.LINE_OPTION, L)
        o = L[FP.LINE_OPTION]
        self.assertTrue(np.allclose(FP.PAINT["mouth"], o["mouth"]))
        self.assertTrue(np.allclose(FP.PAINT["nose_tip"], o["nose_tip"]))
        self.assertEqual(FP.LINE_THICK, o["thick"])


class ShadowDesignTests(unittest.TestCase):
    def normals(self, size=64):
        """A sphere's front half laid out like the face map (nx by u, nz by v)."""
        c = (np.arange(size) + 0.5) / size
        a = np.radians((c - 0.5) * 180.0)
        e = np.radians((0.5 - c) * 120.0)
        A, E = np.meshgrid(a, e)
        return np.stack([np.sin(A) * np.cos(E), -np.cos(A) * np.cos(E), np.sin(E)], axis=2)

    def test_near_side_lit_longest_and_monotonic(self):
        fr = frame()
        N = self.normals()
        qu = np.full(N.shape[:2], 0.05)  # away from the nose
        qv = np.full(N.shape[:2], 1.70)
        t = FS.theta_dark(N, qu, qv, fr)
        mid = N.shape[0] // 2
        row = t[mid]
        self.assertTrue((np.diff(row) > 0).all())  # +X (light side) goes dark last
        self.assertAlmostEqual(float(t[mid, N.shape[1] // 2 - 1] + t[mid, N.shape[1] // 2]) / 2, 95.7, delta=4.0)
        thr = np.clip(t / 180.0, 0, 1)
        prev = None
        for a in np.arange(0.0, 180.1, 2.5):
            lit = FS.lit_mask(thr, a) > 0.5
            if prev is not None:
                self.assertFalse((lit & ~prev).any(), a)  # nested: never lit again once dark
            prev = lit

    def test_nose_shadow_on_the_far_side(self):
        fr = frame()
        N = np.zeros((1, 3, 3))
        N[..., 1] = -1.0  # facing forward
        nv = fr.nose[1]
        qu = np.array([[-0.0025, 0.0025, -0.02]])
        qv = np.array([[nv - 0.0005, nv - 0.0005, nv]])
        t = FS.theta_dark(N, qu, qv, fr)
        self.assertLess(t[0, 0], FS.NOSE_DARK[1] + 1)  # far side of the nose: early
        self.assertGreater(t[0, 1], 90)  # near side: the base
        self.assertGreater(t[0, 2], 90)

    def test_bridge_stroke_on_the_far_side(self):
        """The nose bridge's shadow stroke: on the far (-X) side of the
        bridge, dark from BRIDGE_DARK degrees, so the key's own side (and,
        mirrored, the other side for a key on -X) stays lit; crisp edges."""
        fr = frame()
        km = fr.st["mouth_k"]
        nv = float(fr.nose[1])
        (u0, v0, r0), (u1, v1, r1) = FS.BRIDGE
        um, vm = 0.5 * (u0 + u1) * km, nv + 0.5 * (v0 + v1) * km
        texel = 0.0002
        qu = np.array([[um, -um, um - 0.004, um]])
        qv = np.array([[vm, vm, vm, nv + v1 * km + 0.003]])
        t = FS.with_bridge(np.full(qu.shape, 95.0), qu, qv, fr, texel)
        self.assertAlmostEqual(float(t[0, 0]), FS.BRIDGE_DARK, places=6)  # on the stroke
        self.assertEqual(float(t[0, 1]), 95.0)  # the near side of the bridge
        self.assertEqual(float(t[0, 2]), 95.0)  # beside it, on the cheek
        self.assertEqual(float(t[0, 3]), 95.0)  # above its top end (a short stroke)
        self.assertLess(u0, 0.0)
        self.assertLess(u1, 0.0)
        self.assertLess(FS.BRIDGE_DARK, FS.NOSE_DARK[0])  # shows before the tip's wedge does
        # Never lights a texel the map already darkens earlier (min), so the
        # sweep stays nested.
        early = FS.with_bridge(np.full(qu.shape, 3.0), qu, qv, fr, texel)
        self.assertTrue((early == 3.0).all())
        # Its edge is about one texel wide.
        c = FS.bridge_cover(np.array([um - (r0 + r1) * 0.5 * km - 2 * texel, um]), np.array([vm, vm]), fr, texel)
        self.assertEqual(float(c[0]), 0.0)
        self.assertEqual(float(c[1]), 1.0)


def _glb(path):
    with open(path, "rb") as fh:
        data = fh.read()
    n = struct.unpack("<I", data[12:16])[0]
    js = json.loads(data[20:20 + n])
    return js, data[28 + n:]


def _acc(js, bin_, i):
    a = js["accessors"][i]
    bv = js["bufferViews"][a["bufferView"]]
    dt = {5126: np.float32, 5123: np.uint16, 5125: np.uint32, 5121: np.uint8}[a["componentType"]]
    k = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[a["type"]]
    off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    return np.frombuffer(bin_, dtype=dt, count=a["count"] * k, offset=off).reshape(a["count"], k)


@unittest.skipUnless(os.path.exists(MANIFEST), f"no exported characters in {ASSETS}")
class FaceAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(MANIFEST, encoding="utf-8") as f:
            cls.manifest = json.load(f)

    def bodies(self):
        out = [(k, v) for k, v in self.manifest["assets"].items() if v.get("kind") == "body" and v.get("face")]
        if not out:
            self.skipTest("no body with face textures exported")
        return out

    def test_textures_exist_with_their_sizes(self):
        for key, entry in self.bodies():
            f = entry["face"]
            self.assertEqual(f["size"], [FP.COLOUR_SIZE, FP.MASK_SIZE])
            col = FP.read_png(os.path.join(ASSETS, f["colour"]))
            mask = FP.read_png(os.path.join(ASSETS, f["mask"]))
            self.assertEqual(col.shape, (FP.COLOUR_SIZE, FP.COLOUR_SIZE, 4), key)
            self.assertEqual(mask.shape, (FP.MASK_SIZE, FP.MASK_SIZE, 4), key)
            # Every channel carries something.
            for c in range(4):
                self.assertGreater(int(mask[..., c].max()), 128, (key, c))
            self.assertEqual(int(col[..., 3].max()), 255)
            self.assertEqual(int(col[..., 3].min()), 0)
            # Look pass 2: the mask's A holds the flat paint at 192 and the glow above it.
            a = mask[..., 3]
            self.assertGreater(int((a == 192).sum()), 200, key)
            self.assertGreater(int((a > 240).sum()), 20, key)

    def test_mouth_width_and_nose_to_mouth_gap(self):
        """The exported faces as measured in review (1024 px texture,
        2026-11-01): mouths 168 (male) and 138 (female) px wide, the nose
        mark 71 px above the male's mouth line against 172 on the female.
        Now both a little wider and the male's mouth about 60 px lower."""
        got = {}
        for key, entry in self.bodies():
            col = FP.read_png(os.path.join(ASSETS, entry["face"]["colour"])).astype(float) / 255.0
            R, A = col[..., 0], col[..., 3]
            mid = col.shape[1] // 2
            line = R[:, mid - 1:mid + 1].mean(axis=1)
            rows = [r for r in range(300, 900) if line[r] < 0.985 and A[r, mid] > 0.9]
            groups = []
            for r in rows:
                if groups and r - groups[-1][-1] <= 2:
                    groups[-1].append(r)
                else:
                    groups.append([r])
            mouth = min(groups, key=lambda g: line[g].min())
            nose = [g for g in groups if g[-1] < mouth[0]][-1]
            m_row = mouth[int(np.argmin(line[mouth]))]
            n_row = nose[int(np.argmin(line[nose]))]
            band = R[mouth[0]:mouth[-1] + 1]
            xs = np.nonzero((band < 0.9).any(axis=0))[0]
            xs = xs[(xs > 300) & (xs < 724)]
            got[key.split("/")[-1]] = (int(xs.max() - xs.min() + 1), m_row - n_row, float(line[n_row]))
        if set(got) != {"body_a", "body_b"}:
            self.skipTest("needs both bodies")
        (wa, ga, na), (wb, gb, nb) = got["body_a"], got["body_b"]
        self.assertGreater(wa, 180, got)  # 168 before
        self.assertLess(wa, 205, got)
        self.assertGreater(wb, 148, got)  # 138 before
        self.assertLess(wb, 170, got)
        self.assertGreater(ga, 71 + 50, got)  # half to two thirds of the 101 px difference
        self.assertLess(ga, 71 + 68, got)
        self.assertGreater(gb, 160, got)  # hers unchanged
        self.assertLess(gb, 185, got)
        for n in (na, nb):
            self.assertLess(n, 0.8, got)  # a clear nose mark (0.92 before)

    def test_head_uvs_inside_and_symmetric(self):
        for key, entry in self.bodies():
            js, bin_ = _glb(os.path.join(ASSETS, entry["file"]))
            heads = [n for n in js["nodes"] if n.get("extras", {}).get("region") == "head"]
            self.assertEqual(len(heads), 4, key)
            for node in heads:
                self.assertTrue(node["extras"].get("face_texture"), node["name"])
                prim = js["meshes"][node["mesh"]]["primitives"][0]
                uv = _acc(js, bin_, prim["attributes"]["TEXCOORD_0"])
                pos = _acc(js, bin_, prim["attributes"]["POSITION"])
                self.assertTrue((uv >= -1e-6).all() and (uv <= 1 + 1e-6).all(), node["name"])
                # The UVs are the manifest's face map of the vertex positions
                # (symmetric about u = 0.5: FaceMapTests); faces across the back
                # seam are unwrapped onto one edge, so compare the median.
                fm = FP.FaceMap(**{k: entry["face"]["map"][k] for k in ("axis_y", "R0", "z0", "z1", "zc", "zw")})
                P = np.stack([pos[:, 0], -pos[:, 2], pos[:, 1]], axis=1).astype(np.float64)  # glTF -> Blender axes
                want = fm.uv(P)
                err = np.abs(uv[:, 0] - want[:, 0]) + np.abs((1.0 - uv[:, 1]) - want[:, 1])
                front = np.abs(fm.angle(P)) < 90
                self.assertLess(float(np.median(err[front])), 1e-3, node["name"])
                self.assertLess(float(np.percentile(err[front], 99)), 1e-2, node["name"])

    def test_threshold_map_sweeps_monotonically(self):
        for key, entry in self.bodies():
            thr = FP.read_png(os.path.join(ASSETS, entry["face"]["mask"]))[..., 2] / 255.0
            # The face front (the middle of the map) is lit from the front and dark from behind.
            mid = thr[150:360, 160:352]
            self.assertGreater(float((FS.lit_mask(mid, 0.0) > 0.5).mean()), 0.95, key)
            self.assertLess(float((FS.lit_mask(mid, 180.0) > 0.5).mean()), 0.1, key)
            prev = None
            for a in np.arange(0.0, 180.1, 2.5):
                lit = FS.lit_mask(thr, a) > 0.5
                if prev is not None:
                    self.assertFalse((lit & ~prev).any(), (key, a))
                prev = lit
            # Light from the +X side darkens the -X half first.
            lit90 = FS.lit_mask(mid, 90.0)
            self.assertGreater(float(lit90[:, mid.shape[1] // 2 + 20:].mean()), float(lit90[:, :mid.shape[1] // 2 - 20].mean()) + 0.5)


if __name__ == "__main__":
    unittest.main()
