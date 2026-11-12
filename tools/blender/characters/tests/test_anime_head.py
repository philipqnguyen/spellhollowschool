"""The anime head (charlib/mh_head.py): pure data and geometry, no MPFB
needed. (The hairstyles' tests are in test_hair.py.)"""

import unittest

import numpy as np

from charlib import head as H
from charlib import sdf as S


class AnimeHeadShapeTests(unittest.TestCase):
    def rays(self, phi_max=50.0):
        phi, th = np.meshgrid(np.linspace(0.0, phi_max, 8), np.arange(0.0, 360.0, 20.0))
        return H.ray_dirs(phi.ravel(), th.ravel()).reshape(-1, 3)

    def test_scalp_is_the_shared_cage(self):
        """Hair and stiff hats are fitted to head.head_sdf's cranium: both
        anime heads must sit exactly on it over the scalp."""
        from charlib import mh_head as MHH

        d = self.rays()
        cage = S.raycast(H.head_sdf(H.FaceParams()), np.zeros_like(d), d, t_max=30.0)
        for body, hp in MHH.PARAMS.items():
            t = S.raycast(MHH.head_sdf(hp), np.zeros_like(d), d, t_max=30.0)
            self.assertLess(float(np.abs(t - cage).max()), 0.1, body)  # cm

    def test_adult_proportions(self):
        """Chin to crown about the spec's head height; a small pointed chin
        but an adult (not childlike) face: face width at the cheekbones about
        0.6 of the head height, the male jaw wider than the female."""
        from charlib import mh_head as MHH

        for body, hp in MHH.PARAMS.items():
            sdf = MHH.head_sdf(hp)
            top = S.raycast(sdf, np.zeros((1, 3)), np.array([[0.0, 0.0, 1.0]]), t_max=30.0)[0]
            self.assertAlmostEqual(top - hp.chin_u, 23.5, delta=0.6, msg=body)
            w = MHH._curve(hp.width)
            cheek = float(w(np.array([-2.0]))[0]) * 2
            self.assertGreater(cheek / 23.5, 0.55, body)
            self.assertLess(cheek / 23.5, 0.65, body)
        wa = MHH._curve(MHH.PARAMS["body_a"].width)(np.array([-8.0]))[0]
        wb = MHH._curve(MHH.PARAMS["body_b"].width)(np.array([-8.0]))[0]
        self.assertGreater(wa, wb)

    def test_flat_profile(self):
        """No forward mouth: the profile falls back steadily from the nose to
        the chin."""
        from charlib import mh_head as MHH

        for body, hp in MHH.PARAMS.items():
            f = MHH._curve(hp.front)
            u = np.linspace(-4.5, hp.chin_u, 20)
            self.assertTrue((np.diff(f(u)) <= 1e-6).all(), body)

    def test_face_keys_are_parameter_offsets(self):
        from charlib import mh_head as MHH

        for name, d in MHH.FACE_KEYS.items():
            for k in d:
                self.assertTrue(hasattr(MHH.HeadParams(), k), (name, k))


if __name__ == "__main__":
    unittest.main()
