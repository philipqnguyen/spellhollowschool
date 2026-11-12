"""A three-strand plait built as geometry (character-look.md section 2): the
braid of the Low Side Braid, laid along a path that rests on the body.

The Meshy guides draw braids as low relief lying on the bust, which
segmentation cuts flat (the braid's chest side is the guide's skin), so the
plait itself is made here and joined to the guide's head hair at prepare
time (prepare_hair_meshy.py `braid`):

* three strands, each a tube of elliptic section, weave round the path
  centre line on the classic 1:2 Lissajous (lateral sin t, depth sin 2t,
  phases a third of a turn apart): every crossing has one strand in front
  of the other, so the plait reads as interlocking sections from the front,
  the back and the sides;
* the strands swell where they cross the middle of the face and thin where
  they turn at the edges (the round, plump sections of a real plait), and
  the whole plait tapers from the root to the tie;
* a dark tie (part TIE) and a tuft of pointed locks below it (part TAIL).

Pure numpy: `plait` returns vertices, triangles, part labels and the
root-to-tip value of every vertex.
"""

from __future__ import annotations

import math

import numpy as np

TAIL, TIE = 4, 5


def resample(P: np.ndarray, ds: float) -> np.ndarray:
    """The polyline P resampled at even arc-length steps of about `ds`."""
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    n = max(2, int(round(s[-1] / ds)) + 1)
    t = np.linspace(0.0, s[-1], n)
    return np.stack([np.interp(t, s, P[:, k]) for k in range(3)], axis=1)


def catmull_rom(C: np.ndarray, per: int = 24) -> np.ndarray:
    """A centripetal-free (uniform) Catmull-Rom curve through the control
    points C, `per` samples per span."""
    C = np.asarray(C, dtype=np.float64)
    Q = np.vstack([2 * C[0] - C[1], C, 2 * C[-1] - C[-2]])
    out = []
    for i in range(1, len(Q) - 2):
        p0, p1, p2, p3 = Q[i - 1], Q[i], Q[i + 1], Q[i + 2]
        for t in np.linspace(0.0, 1.0, per, endpoint=False):
            t2, t3 = t * t, t * t * t
            out.append(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                              + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    out.append(C[-1])
    return np.array(out)


def frames(P: np.ndarray, out: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per path point: tangent T, lateral W (across the braid's face) and
    O (out of the face, toward `out`), orthonormal."""
    T = np.gradient(P, axis=0)
    T /= np.maximum(np.linalg.norm(T, axis=1, keepdims=True), 1e-12)
    O = out - (out * T).sum(axis=1, keepdims=True) * T
    O /= np.maximum(np.linalg.norm(O, axis=1, keepdims=True), 1e-12)
    W = np.cross(T, O)
    return T, W, O


def _tube(C: np.ndarray, up: np.ndarray, ra: np.ndarray, rb: np.ndarray, ring: int,
          cap0: bool = True, cap1: bool = True) -> tuple[np.ndarray, np.ndarray]:
    """A closed tube along the curve C (n, 3): elliptic rings of semi-axes
    `ra` (across, perpendicular to `up`) and `rb` (along `up`'s part
    perpendicular to the curve); the ends closed by a fan to a point."""
    n = len(C)
    t = np.gradient(C, axis=0)
    t /= np.maximum(np.linalg.norm(t, axis=1, keepdims=True), 1e-12)
    v = up - (up * t).sum(axis=1, keepdims=True) * t
    v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
    u = np.cross(v, t)
    ph = np.linspace(0.0, 2 * math.pi, ring, endpoint=False)
    V = (C[:, None, :] + ra[:, None, None] * np.cos(ph)[None, :, None] * u[:, None, :]
         + rb[:, None, None] * np.sin(ph)[None, :, None] * v[:, None, :]).reshape(-1, 3)
    F = []
    for i in range(n - 1):
        for j in range(ring):
            a, b = i * ring + j, i * ring + (j + 1) % ring
            c, d = a + ring, b + ring
            F += [(a, c, b), (b, c, d)]
    V = list(V)
    if cap0:
        k = len(V)
        V.append(C[0] - t[0] * 0.25 * min(ra[0], rb[0]))
        F += [(k, j, (j + 1) % ring) for j in range(ring)]
    if cap1:
        k = len(V)
        V.append(C[-1] + t[-1] * 0.25 * min(ra[-1], rb[-1]))
        base = (n - 1) * ring
        F += [(k, base + (j + 1) % ring, base + j) for j in range(ring)]
    return np.array(V), np.array(F, dtype=np.int64)


def plait(P: np.ndarray, out: np.ndarray, *, half_width: float, half_depth: float, period: float,
          strand_a: float, strand_b: float, ring: int = 10, ds: float = 0.0025, root_len: float = 0.03,
          end_scale: float = 0.72, swell: float = 0.3, tie_len: float = 0.009, tuft_len: float = 0.055,
          tuft_locks: int = 7, seed: int = 3, end_len: float = 0.03,
          root_open: float = 0.0, root_swell: float = 1.0) -> dict:
    """The plait along path P (n, 3; root first, the tie at its end) with
    its face turned toward `out` (n, 3: per point the direction away from
    the body). `half_width` / `half_depth`: the weave's lateral and depth
    amplitudes; `strand_a` / `strand_b`: a strand's semi-axes across and
    out of the face; `period`: the length over which each strand weaves
    once from side to side and back (three sections a side). The weave
    opens out of a gathered root over `root_len` (or with `root_open` > 1
    starts that much wider and slack, its strands `root_swell` fuller, and
    tightens over `root_len`) and tapers to `end_scale` at the tie. Returns {"V", "F", "part", "rtt"}."""
    Pr = resample(P, ds)
    n = len(Pr)
    # The out direction at the resampled points.
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    s0 = np.concatenate([[0.0], np.cumsum(seg)])
    s = np.linspace(0.0, s0[-1], n)
    Oi = np.stack([np.interp(s, s0, out[:, k]) for k in range(3)], axis=1)
    T, W, O = frames(Pr, Oi)
    L = s[-1]
    grow = np.clip(s / max(root_len, 1e-6), 0.0, 1.0)
    grow = grow * grow * (3 - 2 * grow)
    # A loose root (root_open > 1): the weave starts wide and slack out of
    # the gathered hair and tightens over its first crossings.
    grow = root_open + (1.0 - root_open) * grow if root_open > 0 else 0.45 + 0.55 * grow
    rloose = 1.0 + (root_swell - 1.0) * (1.0 - np.clip(s / max(root_len, 1e-6), 0.0, 1.0)) ** 2
    taper = 1.0 + (end_scale - 1.0) * (s / L) ** 1.3
    # Gathered into the tie over the last `end_len`.
    gend = np.clip((L - s) / max(end_len, 1e-6), 0.0, 1.0)
    gend = gend * gend * (3 - 2 * gend)
    amp = grow * taper * (0.4 + 0.6 * gend)
    rgather = 0.8 + 0.2 * gend
    Vs, Fs, parts, rtts = [], [], [], []
    off = 0
    w = 2 * math.pi / period
    for k in range(3):
        th = w * s + 2 * math.pi * k / 3
        lat = half_width * amp * np.sin(th)
        dep = half_depth * amp * np.sin(2 * th)
        C = Pr + lat[:, None] * W + dep[:, None] * O
        # Plump where a strand crosses the face's middle, thin where it
        # turns at an edge.
        sw = 1.0 + swell * (np.cos(th) ** 2 - 0.5) * 2
        ra = strand_a * taper * sw * rgather * rloose
        rb = strand_b * taper * (0.9 + 0.2 * np.cos(th) ** 2) * rgather * rloose
        V, F = _tube(C, O, ra, rb, ring)
        Vs.append(V)
        Fs.append(F + off)
        off += len(V)
        parts.append(np.full(len(V), TAIL, dtype=np.uint8))
        # Root-to-tip along the path (the caps take their ring's value).
        r = np.repeat(s / L, ring)
        r = np.concatenate([r, [0.0, 1.0]])
        rtts.append(r)
    # The tie: a short band round the braid's end.
    end_r = (half_width * 0.4 + strand_a * 0.8 * (1.0 + swell)) * end_scale * 1.05
    tC = np.stack([Pr[-1] + T[-1] * x for x in np.linspace(-tie_len * 0.5, tie_len * 0.5, 4)])
    tV, tF = _tube(tC, np.repeat(O[-1:], 4, axis=0), np.full(4, end_r), np.full(4, end_r * 0.8), 14)
    Vs.append(tV)
    Fs.append(tF + off)
    off += len(tV)
    parts.append(np.full(len(tV), TIE, dtype=np.uint8))
    rtts.append(np.full(len(tV), 0.9))
    # The tuft: pointed locks out of the tie, close together, falling on
    # and drawn to points.
    rng = np.random.default_rng(seed)
    base = Pr[-1] + T[-1] * tie_len * 0.2
    for j in range(tuft_locks):
        ang = 2 * math.pi * (j + 0.25 * rng.random()) / tuft_locks
        o = math.cos(ang) * W[-1] + math.sin(ang) * O[-1] * 0.6
        ln = tuft_len * (0.7 + 0.4 * rng.random())
        m = 16
        q = np.linspace(0.0, 1.0, m)
        # Out of the tie a little, then falling nearly straight.
        spread = end_r * (0.3 + 0.75 * np.sin(np.minimum(q * 2.0, 1.0) * math.pi / 2))
        C = base + o[None, :] * spread[:, None] + T[-1][None, :] * (q * ln)[:, None]
        C += O[-1][None, :] * (-0.003 * q ** 2)[:, None]
        r0 = end_r * 0.55
        ra = r0 * (1.0 - q) ** 1.1 + 0.0003
        rb = ra * 0.65
        V, F = _tube(C, np.repeat(O[-1:], m, axis=0), ra, rb, 8, cap1=True)
        Vs.append(V)
        Fs.append(F + off)
        off += len(V)
        parts.append(np.full(len(V), TAIL, dtype=np.uint8))
        rr = 0.9 + 0.1 * np.repeat(q, 8)
        rtts.append(np.concatenate([rr, [0.9, 1.0]]))
    return {"V": np.vstack(Vs), "F": np.vstack(Fs), "part": np.concatenate(parts),
            "rtt": np.concatenate(rtts), "path": Pr, "T": T, "O": O, "W": W, "hw": half_width}


def lock(P: np.ndarray, out: np.ndarray, width: np.ndarray, thick: np.ndarray, ring: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """A flat lock of hair along P (n, 3): a closed tube of elliptic section,
    `width` (n) across and `thick` (n) out of the surface it lies on (`out`,
    n x 3, away from the body)."""
    return _tube(P, out, 0.5 * np.asarray(width), 0.5 * np.asarray(thick), ring)
