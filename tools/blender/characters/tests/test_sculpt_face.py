"""The painted face on a sculpt-wrapped head and the fringe refit to it
(charlib/mh_face.py sculpt_frame, charlib/gear_ctx.py phi_warp): pure
geometry, no MPFB or sculpts needed."""

import math
import unittest

import numpy as np

from charlib import gear_ctx as GC
from charlib import mh_face as MF
from charlib.proportions import get as body_of


def frame(body_id: str = "body_b", sock_w: float = 0.046, head_h: float = 0.25) -> "MF.FaceFrame":
    """A face frame like sculpt_frame makes, on a cylinder (no mesh)."""
    b = body_of(body_id)
    st, k, km = MF.sculpt_style(b, sock_w, 0.04, head_h)
    fr = MF.FaceFrame(b, 0.05, 0.09, np.array([0.035, 1.53]), np.array([0.0, 1.46]), np.array([0.0, 1.49]),
                      np.array([0.0, 0.05, 1.50]), np.array([0.09, 0.10, 0.30]), 1.42, 1.58, st=st, k=k)
    MF.brow_anchors(st, k)
    fr.world_polys()
    return fr


def on_cylinder(fr, uv: np.ndarray) -> np.ndarray:
    a = uv[:, 0] / fr.R0
    return np.stack([fr.R0 * np.sin(a), fr.axis_y - fr.R0 * np.cos(a), uv[:, 1]], axis=1)


class SculptFaceTests(unittest.TestCase):
    def test_eye_sized_to_the_socket(self):
        for body in ("body_a", "body_b"):
            b = body_of(body)
            for w in (0.04, 0.05):
                st, k, _ = MF.sculpt_style(b, w, 0.03, 0.24)
                self.assertAlmostEqual(st["eye_w"], MF.SCULPT_EYE[body]["fill"] * w, places=9)
                # Everything of the eye scales together (the design keeps its proportions).
                self.assertAlmostEqual(st["iris_rx"] / st["eye_w"], MF.STYLE[body]["iris_rx"] / MF.STYLE[body]["eye_w"])
                # Thin strokes are refined finer than they are thick.
                self.assertLess(st["zone_edge"]["crease"], 2 * st["crease_th"][0] + st["crease_th"][1])
                self.assertLess(st["zone_edge"]["brow"], 2.0 * st["brow_min"])

    def test_ellipsoid_round_a_fixed_centre(self):
        c = np.array([0.0, 0.04, 1.5])
        r = np.array([0.085, 0.10, 0.14])
        rng = np.random.default_rng(1)
        u = rng.uniform(-0.9, 0.9, 400)
        v = rng.uniform(-0.6, 0.4, 400)
        d = np.stack([np.sin(u) * np.cos(v), -np.cos(u) * np.cos(v), np.sin(v)], axis=1)  # the front half
        Q = c + d * r
        cen, rr, rms = MF.fit_face_ellipsoid(Q, c)
        np.testing.assert_allclose(rr, r, rtol=1e-6)
        self.assertLess(rms, 1e-6)
        # A flat front cannot ask for an infinite radius.
        flat = Q.copy()
        flat[:, 2] = 1.5 + (flat[:, 2] - 1.5) * 0.02
        flat[:, 1] = c[1] - np.sqrt(np.maximum(r[1] ** 2 * (1 - (flat[:, 0] / r[0]) ** 2), 1e-6))
        _, rf, _ = MF.fit_face_ellipsoid(flat, c)
        self.assertTrue(np.isfinite(rf).all())

    def test_blink_closes_the_eye_and_leaves_the_brow(self):
        fr = frame()
        e = MF.EyeShape(fr.st)
        s = np.linspace(e.s_in + 0.001, e.s_out - 0.001, 9)
        t_open = 0.5 * (e.upper(s) + e.lower(s))
        lash = e.upper(s) + 0.5 * e.lash_th(s)
        brow_t = np.full_like(s, fr.st["brow_v"])
        loc = np.vstack([np.stack([s, t_open], 1), np.stack([s, lash], 1), np.stack([s, brow_t], 1)])
        uv = np.stack([fr.eye[0] + loc[:, 0], fr.eye[1] + loc[:, 1]], 1)
        P = on_cylinder(fr, uv)
        keys = MF.eye_keys(fr, P, [], np.ones(len(P)))
        moved = keys["blink_l"] - P
        n = len(s)
        low = e.lower(s)
        # The opening collapses onto the lower lid, the lash line comes down with it ...
        self.assertTrue((fr.eye_local(keys["blink_l"][:n], 1)[:, 1] - low < 0.0008).all())
        self.assertTrue((fr.eye_local(keys["blink_l"][n:2 * n], 1)[:, 1] - low < 0.002 * fr.k + 0.002).all())
        # ... the brow stays where it is and the right eye does not move.
        self.assertLess(np.abs(moved[2 * n:]).max(), 0.0004)
        self.assertLess(np.abs(keys["blink_r"] - P).max(), 1e-9)


class FringeRefitTests(unittest.TestCase):
    def tearDown(self):
        GC.FACE_PHI.pop("body_b", None)

    def test_identity_without_a_registered_face(self):
        b = body_of("body_b")
        phi = np.linspace(0, 180, 37)
        np.testing.assert_allclose(GC.phi_warp(b, phi, np.zeros_like(phi)), phi)

    def test_brow_and_lash_line_land_on_the_face(self):
        b = body_of("body_b")
        ref = GC.ref_face_phi("body_b")
        self.assertTrue(40.0 < ref[0] < ref[1] < ref[2] < 110.0, ref)
        GC.FACE_PHI["body_b"] = (ref[0] - 6.0, ref[1] - 8.0, ref[2] - 7.0)
        out = GC.phi_warp(b, np.array(ref), np.zeros(3))
        np.testing.assert_allclose(out, [ref[0] - 6.0, ref[1] - 8.0, ref[2] - 7.0], atol=1e-9)
        # Monotonic down the front; the crown and the back of the head unchanged.
        phi = np.linspace(0, 180, 181)
        w = GC.phi_warp(b, phi, np.zeros_like(phi))
        self.assertTrue((np.diff(w) > 0).all())
        np.testing.assert_allclose(GC.phi_warp(b, phi, np.full_like(phi, 180.0)), phi)
        self.assertAlmostEqual(float(GC.phi_warp(b, 20.0, 0.0)), 20.0)


if __name__ == "__main__":
    unittest.main()
