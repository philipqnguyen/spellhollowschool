"""Tube frames along polylines (rotation-minimising) and ring ray directions."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .skeleton import unit


@dataclass
class Ring:
    centre: np.ndarray
    tangent: np.ndarray
    normal: np.ndarray  # direction of angle 0
    binormal: np.ndarray  # direction of angle 90 degrees
    aspect: tuple[float, float] = (1.0, 1.0)
    aspect_frame: tuple[np.ndarray, np.ndarray] | None = None

    def dirs(self, angles: np.ndarray) -> np.ndarray:
        """Ray directions for column angles (radians)."""
        v = np.cos(angles)[:, None] * self.normal + np.sin(angles)[:, None] * self.binormal
        if self.aspect_frame is not None and self.aspect != (1.0, 1.0):
            e1, e2 = self.aspect_frame
            x = v @ e1
            y = v @ e2
            v = (x * self.aspect[0])[:, None] * e1 + (y * self.aspect[1])[:, None] * e2
            v = v / np.linalg.norm(v, axis=1, keepdims=True)
        return v


class Polyline:
    """Arc-length parametrised polyline with smoothed tangents at the joints."""

    def __init__(self, points: list[np.ndarray], smooth: float = 0.02):
        self.p = np.asarray(points, dtype=np.float64)
        seg = np.diff(self.p, axis=0)
        self.len = np.linalg.norm(seg, axis=1)
        self.dir = seg / self.len[:, None]
        self.cum = np.concatenate([[0.0], np.cumsum(self.len)])
        self.smooth = smooth

    @property
    def length(self) -> float:
        return float(self.cum[-1])

    def point(self, s: float) -> np.ndarray:
        s = float(np.clip(s, 0.0, self.length))
        i = int(np.clip(np.searchsorted(self.cum, s, side="right") - 1, 0, len(self.len) - 1))
        return self.p[i] + self.dir[i] * (s - self.cum[i])

    def tangent(self, s: float) -> np.ndarray:
        """Direction at s, blended across joints within +-smooth."""
        acc = np.zeros(3)
        for j in range(len(self.len)):
            a, b = self.cum[j], self.cum[j + 1]
            lo, hi = max(a, s - self.smooth), min(b, s + self.smooth)
            if hi > lo:
                acc += self.dir[j] * (hi - lo)
        if not acc.any():
            j = 0 if s <= 0 else len(self.len) - 1
            return self.dir[j]
        return unit(acc)


def rmf(tangents: list[np.ndarray], n0: np.ndarray) -> list[np.ndarray]:
    """Rotation-minimising normals for a sequence of tangents (parallel transport:
    each normal is turned by the rotation that takes one tangent to the next)."""
    from .skeleton import rotate

    normals = []
    n = unit(n0 - tangents[0] * float(n0 @ tangents[0]))
    normals.append(n)
    for t0, t1 in zip(tangents, tangents[1:]):
        axis = np.cross(t0, t1)
        s = float(np.linalg.norm(axis))
        if s > 1e-9:
            angle = math.atan2(s, float(t0 @ t1))
            n = rotate(n, axis / s, angle)
        n = unit(n - t1 * float(n @ t1))
        normals.append(n)
    return normals


def tube_rings(line: Polyline, s_values, n0: np.ndarray) -> list[Ring]:
    ts = [line.tangent(s) for s in s_values]
    ns = rmf(ts, n0)
    return [Ring(line.point(s), t, n, unit(np.cross(t, n))) for s, t, n in zip(s_values, ts, ns)]


def angles_around(points: np.ndarray, ring: Ring) -> np.ndarray:
    """Angles (radians, 0..2pi) of points around a ring's axis in its frame."""
    d = points - ring.centre
    d = d - np.outer(d @ ring.tangent, ring.tangent)
    a = np.arctan2(d @ ring.binormal, d @ ring.normal)
    return np.mod(a, 2 * np.pi)


def project_to_surface(sdf, p: np.ndarray, iters: int = 8) -> np.ndarray:
    """Move points onto the zero set along the SDF gradient (Newton steps)."""
    from .sdf import gradient

    q = np.asarray(p, dtype=np.float64).copy()
    for _ in range(iters):
        d = sdf(q)
        g = gradient(sdf, q)
        q = q - g * d[:, None]
    return q
