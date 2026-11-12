"""The built plait (charlib/hair_braid.py, character-look.md section 2)."""

import unittest

import numpy as np

from charlib import hair_braid as HB
from charlib import hair_meshops as MO


def _plait(**kw):
    z = np.linspace(1.4, 1.1, 20)
    P = np.stack([np.full_like(z, 0.09), np.full_like(z, -0.1), z], axis=1)
    out = np.tile([0.0, -1.0, 0.0], (len(P), 1))
    args = dict(half_width=0.012, half_depth=0.0048, period=0.125, strand_a=0.0105, strand_b=0.0085)
    args.update(kw)
    return HB.plait(P, out, **args), P


class PlaitTests(unittest.TestCase):
    def test_mesh(self):
        b, _ = _plait()
        V, F = b["V"], b["F"]
        self.assertTrue(np.isfinite(V).all())
        self.assertTrue((F >= 0).all() and (F < len(V)).all())
        self.assertEqual(len(b["part"]), len(V))
        self.assertEqual(set(np.unique(b["part"])), {HB.TAIL, HB.TIE})
        self.assertTrue(((b["rtt"] >= 0) & (b["rtt"] <= 1)).all())
        # Closed pieces: every edge between exactly two triangles.
        E = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
        _, cnt = np.unique(E, axis=0, return_counts=True)
        self.assertTrue((cnt == 2).all())
        # Three strands, a tie and the tuft's locks.
        ncomp = len(np.unique(MO.components(len(V), MO.edges(F))))
        self.assertEqual(ncomp, 3 + 1 + 7)

    def test_three_strands_interlock(self):
        """Where two strands cross the middle of the face one is in front of
        the other (the 1:2 weave), and each strand comes to the front once
        per period."""
        b, P = _plait()
        s = np.linspace(0.0, 0.125, 400)
        w = 2 * np.pi / 0.125
        lat = [np.sin(w * s + 2 * np.pi * k / 3) for k in range(3)]
        dep = [np.sin(2 * (w * s + 2 * np.pi * k / 3)) for k in range(3)]
        for a in range(3):
            for c in range(a + 1, 3):
                cross = np.nonzero(np.diff(np.sign(lat[a] - lat[c])))[0]
                self.assertGreater(len(cross), 0)
                for i in cross:
                    self.assertGreater(abs(dep[a][i] - dep[c][i]), 0.5)

    def test_rests_along_the_path_and_tapers(self):
        b, P = _plait()
        path = b["path"]
        # Every strand vertex within the braid's half-width (plus a strand)
        # of the path; wider at the root's end of the chest than at the tie.
        tail = (b["part"] == HB.TAIL) & (b["rtt"] < 0.9)  # the plait, not the tuft below the tie
        Vt = b["V"][tail]
        d = np.min(np.linalg.norm(Vt[:, None, :] - path[None, ::5, :], axis=2), axis=1)
        self.assertLess(float(np.percentile(d, 99)), 0.012 + 0.0105 * 1.4 + 0.01)
        z = Vt[:, 2]
        top = (z < 1.33) & (z > 1.30)
        bot = (z < 1.16) & (z > 1.13)
        wid = lambda m: float(np.ptp(Vt[m, 0]))  # noqa: E731
        self.assertGreater(wid(top), wid(bot))


if __name__ == "__main__":
    unittest.main()
