"""Signed distance functions and ray casting, vectorised with numpy.

Body parts are sculpted as smooth unions of simple primitives (ellipsoids,
tapered capsules) and then sampled by casting rays from inside the part (from
the head centre, the spine axis, a limb axis) to the first point where the
distance turns positive. Sampling the same rays for different parameter sets
gives meshes with identical topology, which is how shape keys are made.

Every function takes points as an (n, 3) float array and returns (n,) distances.
Distances of smooth unions and ellipsoids are bounds, not exact, so the marcher
steps by a fraction of the distance.
"""

from __future__ import annotations

import numpy as np


def _arr(v) -> np.ndarray:
    return np.asarray(v, dtype=np.float64)


def ellipsoid(p: np.ndarray, centre, radii) -> np.ndarray:
    """Bound on the distance to an axis-aligned ellipsoid (Quilez)."""
    q = (p - _arr(centre)) / _arr(radii)
    k0 = np.linalg.norm(q, axis=1)
    k1 = np.linalg.norm(q / _arr(radii), axis=1)
    k1 = np.maximum(k1, 1e-9)
    return k0 * (k0 - 1.0) / k1


def ellipsoid_rot(p: np.ndarray, centre, radii, basis) -> np.ndarray:
    """Ellipsoid whose axes are the rows of `basis` (a 3x3 orthonormal matrix)."""
    local = (p - _arr(centre)) @ _arr(basis).T
    return ellipsoid(local, (0.0, 0.0, 0.0), radii)


def sphere(p: np.ndarray, centre, r: float) -> np.ndarray:
    return np.linalg.norm(p - _arr(centre), axis=1) - r


def capsule(p: np.ndarray, a, b, ra: float, rb: float | None = None) -> np.ndarray:
    """Capsule from `a` to `b` whose radius goes linearly from `ra` to `rb`."""
    rb = ra if rb is None else rb
    a = _arr(a)
    b = _arr(b)
    pa = p - a
    ba = b - a
    h = np.clip((pa @ ba) / max(float(ba @ ba), 1e-12), 0.0, 1.0)
    return np.linalg.norm(pa - h[:, None] * ba, axis=1) - (ra + (rb - ra) * h)


def flat_capsule(p: np.ndarray, a, b, ra, rb, up) -> np.ndarray:
    """Capsule with an elliptical section: `ra`/`rb` are (width, thickness) at
    each end and `up` is the thickness direction (perpendicular to b - a)."""
    a = _arr(a)
    b = _arr(b)
    ba = b - a
    length = float(np.linalg.norm(ba))
    axis = ba / length
    up = _arr(up)
    up = up - axis * float(up @ axis)
    up /= np.linalg.norm(up)
    side = np.cross(axis, up)
    pa = p - a
    along = pa @ axis
    h = np.clip(along / length, 0.0, 1.0)
    w = ra[0] + (rb[0] - ra[0]) * h
    t = ra[1] + (rb[1] - ra[1]) * h
    rest = pa - (h * length)[:, None] * axis
    qs = (rest @ side) / w
    qu = (rest @ up) / t
    # Rounded ends: overshoot past either end counts against the thinner radius.
    over = (along - h * length) / np.minimum(w, t)
    k = np.sqrt(qs * qs + qu * qu + over * over)
    return (k - 1.0) * np.minimum(w, t)


def plane(p: np.ndarray, point, normal) -> np.ndarray:
    """Signed distance to a plane; positive on the side `normal` points to."""
    n = _arr(normal)
    n = n / np.linalg.norm(n)
    return (p - _arr(point)) @ n


def smin(a: np.ndarray, b: np.ndarray, k: float) -> np.ndarray:
    """Polynomial smooth union with blend radius k."""
    if k <= 0:
        return np.minimum(a, b)
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0.0, 1.0)
    return b + (a - b) * h - k * h * (1.0 - h)


def smax(a: np.ndarray, b: np.ndarray, k: float) -> np.ndarray:
    """Smooth intersection (use with -b for a smooth subtraction)."""
    return -smin(-a, -b, k)


def union(parts, k: float) -> np.ndarray:
    out = parts[0]
    for d in parts[1:]:
        out = smin(out, d, k)
    return out


def raycast(sdf, origins: np.ndarray, dirs: np.ndarray, t_max: float = 0.6,
            step_scale: float = 0.6, min_step: float = 0.0004, iters: int = 400) -> np.ndarray:
    """Distance along each ray (from inside the solid) to its first exit.

    `sdf` maps (n, 3) points to (n,) distances. Rays that start outside return
    0; rays that never exit return `t_max`.
    """
    origins = _arr(origins)
    dirs = _arr(dirs)
    n = len(origins)
    t = np.zeros(n)
    lo = np.zeros(n)
    hi = np.full(n, t_max)
    active = np.ones(n, dtype=bool)
    d0 = sdf(origins)
    active &= d0 < 0
    for _ in range(iters):
        if not active.any():
            break
        idx = np.nonzero(active)[0]
        d = sdf(origins[idx] + dirs[idx] * t[idx, None])
        inside = d < 0
        # Exited: bracket [lo, t].
        out_idx = idx[~inside]
        hi[out_idx] = t[out_idx]
        active[out_idx] = False
        in_idx = idx[inside]
        lo[in_idx] = t[in_idx]
        t[in_idx] += np.maximum(-d[inside] * step_scale, min_step)
        over = in_idx[t[in_idx] >= t_max]
        hi[over] = t_max
        lo[over] = t_max
        active[over] = False
    # Bisection to refine the exit point.
    for _ in range(24):
        mid = 0.5 * (lo + hi)
        d = sdf(origins + dirs * mid[:, None])
        inside = d < 0
        lo = np.where(inside, mid, lo)
        hi = np.where(inside, hi, mid)
    res = 0.5 * (lo + hi)
    res[d0 >= 0] = 0.0
    return res


def gradient(sdf, p: np.ndarray, eps: float = 1e-4) -> np.ndarray:
    """Normalised numerical gradient (the outward surface normal near the surface)."""
    p = _arr(p)
    g = np.zeros_like(p)
    for i in range(3):
        e = np.zeros(3)
        e[i] = eps
        g[:, i] = sdf(p + e) - sdf(p - e)
    norm = np.linalg.norm(g, axis=1, keepdims=True)
    return g / np.maximum(norm, 1e-12)


def smoothstep(e0: float, e1: float, x):
    t = np.clip((np.asarray(x, dtype=np.float64) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def lerp(a, b, t):
    return a + (b - a) * t
