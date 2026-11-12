"""The Guardian Charm's silver animals (spells to level 20;
docs/architecture.md "Spells and dueling").
Pure Python: the six animals' geometry and their manifest;
`build_guardian.py` turns them into `client/public/assets/guardian/
guardian.glb` with Blender.

Every animal is invented here in the game's toon look: soft, rounded, low
poly shapes lofted along a spine (smooth shaded, no texture, one white
material; the client's silver shader does the rest). Each animal is a few
part nodes `guardian_<animal>_<part>`: `body`, `head`, four legs
`leg_fl|leg_fr|leg_bl|leg_br` or two wings `wing_l|wing_r`, and `tail`.
A part's mesh is in its node's frame, whose origin is the joint the client
swings it about (hip, shoulder, neck, tail root, wing root); the node sits at
that pivot in the animal's frame.

The animal's frame: Y up, metres, facing +z; the ground under the animal is
y = 0 (the owl and the otter float: their manifest `follow_height_m` lifts
them). Left is +x (the client's glTF loader mirrors x into Babylon's
left-handed frame, where an animal facing +z has its left at -x).

At most `TRI_BUDGET` triangles an animal (`triangles()`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

TRI_BUDGET = 1500
ANIMALS = ("hare", "fox", "cat", "owl", "otter", "horse")
GAITS = ("hop", "trot", "glide", "fly", "swim", "gallop")
LEGS = ("leg_fl", "leg_fr", "leg_bl", "leg_br")
WINGS = ("wing_l", "wing_r")

Vec3 = tuple[float, float, float]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a):
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n) if n > 1e-12 else (0.0, 0.0, 0.0)


@dataclass
class Mesh:
    """Triangles over shared vertices (smooth shaded: one vertex per spot)."""

    verts: list = field(default_factory=list)
    tris: list = field(default_factory=list)

    def v(self, p) -> int:
        self.verts.append((float(p[0]), float(p[1]), float(p[2])))
        return len(self.verts) - 1

    def triangle_count(self) -> int:
        return len(self.tris)

    def bounds(self):
        lo = tuple(min(v[k] for v in self.verts) for k in range(3))
        hi = tuple(max(v[k] for v in self.verts) for k in range(3))
        return lo, hi

    def translated(self, d) -> "Mesh":
        return Mesh([_add(v, d) for v in self.verts], list(self.tris))


def loft(m: Mesh, spine, radii, seg: int = 10, up=(0.0, 1.0, 0.0), tips=(True, True), twist=0.0) -> None:
    """A smooth closed blob along `spine` (points), each ring an ellipse of
    `radii[i] = (across, along up)` about the spine; the ends close on a pole
    at the first and last spine point when `tips` says so (the first and last
    radii are then ignored), else with a flat cap. `up` steers which way a
    ring's second radius points (the side across is perpendicular to both)."""
    n = len(spine)
    assert n >= 3 and len(radii) == n
    rings = []
    for i in range(n):
        if (i == 0 and tips[0]) or (i == n - 1 and tips[1]):
            rings.append(None)
            continue
        a, b = spine[max(0, i - 1)], spine[min(n - 1, i + 1)]
        t = _norm(_sub(b, a))
        ref = up if abs(_dot(_norm(up), t)) < 0.95 else (0.0, 0.0, 1.0)
        u = _norm(_cross(ref, t))
        w = _cross(t, u)
        rx, ry = radii[i]
        ring = []
        for k in range(seg):
            ang = 2 * math.pi * k / seg + twist
            p = _add(spine[i], _add(_mul(u, rx * math.cos(ang)), _mul(w, ry * math.sin(ang))))
            ring.append(m.v(p))
        rings.append(ring)
    body = [i for i in range(n) if rings[i] is not None]
    assert len(body) >= 2
    # Wound consistently by construction (every edge used once each way),
    # then the whole blob turned outward by its signed volume.
    tris = []
    for i0, i1 in zip(body, body[1:]):
        r0, r1 = rings[i0], rings[i1]
        for k in range(seg):
            k1 = (k + 1) % seg
            tris.append((r0[k], r0[k1], r1[k1]))
            tris.append((r0[k], r1[k1], r1[k]))
    for first, ring_i, end in ((True, body[0], 0), (False, body[-1], n - 1)):
        ring = rings[ring_i]
        tip = tips[0] if first else tips[1]
        c = m.v(spine[end] if tip else spine[ring_i])
        for k in range(seg):
            k1 = (k + 1) % seg
            tris.append((c, ring[k1], ring[k]) if first else (c, ring[k], ring[k1]))
    if _volume(m.verts, tris) < 0:
        tris = [(a, c, b) for a, b, c in tris]
    m.tris.extend(tris)


def _volume(verts, tris) -> float:
    total = 0.0
    for a, b, c in tris:
        total += _dot(verts[a], _cross(verts[b], verts[c])) / 6.0
    return total


def ellipsoid(m: Mesh, centre, rx: float, ry: float, rz: float, seg: int = 10, rings: int = 6, axis="z") -> None:
    """An ellipsoid as a loft along `axis` (z: forward, y: up, x: across)."""
    pts, rad = [], []
    for i in range(rings + 1):
        a = math.pi * i / rings
        c, s = -math.cos(a), math.sin(a)
        if axis == "z":
            pts.append((centre[0], centre[1], centre[2] + rz * c))
            rad.append((rx * s, ry * s))
        elif axis == "y":
            pts.append((centre[0], centre[1] + ry * c, centre[2]))
            rad.append((rx * s, rz * s))
        else:
            pts.append((centre[0] + rx * c, centre[1], centre[2]))
            rad.append((rz * s, ry * s))
    up = (0.0, 0.0, 1.0) if axis == "y" else (0.0, 1.0, 0.0)
    loft(m, pts, rad, seg, up=up)


def smooth_profile(n: int, peak: float, lo: float = 0.0, bias: float = 0.5) -> list[float]:
    """`n` radii rising from 0 to `peak` and back (a sine bump skewed toward
    `bias` of the length), never below `lo` inside."""
    out = []
    for i in range(n):
        t = i / (n - 1)
        # Skew t so the bump peaks at `bias`.
        s = t / (2 * bias) if t < bias else 0.5 + (t - bias) / (2 * (1 - bias))
        r = peak * math.sin(math.pi * s) ** 0.75
        out.append(max(lo, r) if 0 < i < n - 1 else 0.0)
    return out


def curve(points, n: int):
    """`n` points along a Catmull-Rom curve through `points`."""
    pts = [points[0]] + list(points) + [points[-1]]
    segs = len(points) - 1
    out = []
    for j in range(n):
        u = j / (n - 1) * segs
        s = min(int(u), segs - 1)
        t = u - s
        p0, p1, p2, p3 = pts[s], pts[s + 1], pts[s + 2], pts[s + 3]
        t2, t3 = t * t, t * t * t
        out.append(tuple(0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                                + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3) for k in range(3)))
    return out


def limb(m: Mesh, points, radii, seg: int = 7) -> None:
    """A tapering leg (or ear, or tail) through `points` with a radius at
    each, rounded at both ends."""
    pts = curve(points, max(len(points), 6))
    # Radii along the resampled curve (linear in index).
    rs = []
    for j in range(len(pts)):
        u = j / (len(pts) - 1) * (len(radii) - 1)
        i = min(int(u), len(radii) - 2)
        t = u - i
        rs.append(radii[i] * (1 - t) + radii[i + 1] * t)
    # Pole caps a little beyond the ends.
    d0 = _norm(_sub(pts[0], pts[1]))
    d1 = _norm(_sub(pts[-1], pts[-2]))
    spine = [_add(pts[0], _mul(d0, rs[0] * 0.7))] + pts + [_add(pts[-1], _mul(d1, rs[-1] * 0.7))]
    rad = [(0.0, 0.0)] + [(r, r) for r in rs] + [(0.0, 0.0)]
    loft(m, spine, rad, seg)


def flat(m: Mesh, points, widths, thick: float, seg: int = 8, up=(0.0, 1.0, 0.0)) -> None:
    """A flattened blade (an ear, a wing, a mane, a fan of tail) through
    `points`: `widths` across, `thick` along `up`."""
    pts = curve(points, max(len(points), 6))
    ws = []
    for j in range(len(pts)):
        u = j / (len(pts) - 1) * (len(widths) - 1)
        i = min(int(u), len(widths) - 2)
        t = u - i
        ws.append(widths[i] * (1 - t) + widths[i + 1] * t)
    d0 = _norm(_sub(pts[0], pts[1]))
    d1 = _norm(_sub(pts[-1], pts[-2]))
    spine = [_add(pts[0], _mul(d0, 0.004))] + pts + [_add(pts[-1], _mul(d1, max(0.004, ws[-1] * 0.4)))]
    rad = [(0.0, 0.0)] + [(w, thick * min(1.0, 0.4 + w / max(widths))) for w in ws] + [(0.0, 0.0)]
    loft(m, spine, rad, seg, up=up)


def cone(m: Mesh, base, tip, r: float, seg: int = 6, up=(0.0, 1.0, 0.0), squash: float = 1.0) -> None:
    """A soft cone (a rounded base, a pointed tip): ears and beaks."""
    mid = _mul(_add(base, tip), 0.5)
    back = _sub(base, _mul(_sub(tip, base), 0.15))
    loft(m, [back, base, mid, tip], [(0, 0), (r, r * squash), (r * 0.55, r * 0.55 * squash), (0, 0)], seg, up=up)


@dataclass
class Part:
    name: str
    pivot: Vec3
    mesh: Mesh  # in the part's frame (origin at the pivot)


@dataclass
class Animal:
    key: str
    gait: str
    follow_height_m: float
    parts: list

    def triangles(self) -> int:
        return sum(p.mesh.triangle_count() for p in self.parts)

    def part(self, name: str) -> Part:
        return next(p for p in self.parts if p.name == name)

    def bounds(self):
        lo = [math.inf] * 3
        hi = [-math.inf] * 3
        for p in self.parts:
            a, b = p.mesh.bounds()
            for k in range(3):
                lo[k] = min(lo[k], a[k] + p.pivot[k])
                hi[k] = max(hi[k], b[k] + p.pivot[k])
        return tuple(lo), tuple(hi)

    def size(self) -> Vec3:
        lo, hi = self.bounds()
        return tuple(round(hi[k] - lo[k], 3) for k in range(3))


def _part(name: str, pivot: Vec3, build) -> Part:
    """A part whose geometry `build(m)` draws in the animal's frame; it is
    moved into the part's frame (origin at `pivot`)."""
    m = Mesh()
    build(m)
    return Part(name, pivot, m.translated(_mul(pivot, -1.0)))


def _quad_legs(front: Vec3, back: Vec3, leg_front, leg_back, r_front, r_back, seg=7):
    """Four legs: `front`/`back` are the left joints (x > 0); the right ones
    mirror them. `leg_*` are points relative to the joint."""
    out = []
    for name, joint, path, radii in (("leg_fl", front, leg_front, r_front), ("leg_bl", back, leg_back, r_back)):
        for side, nm in ((1.0, name), (-1.0, name[:-1] + "r")):
            j = (joint[0] * side, joint[1], joint[2])
            pts = [(j[0] + p[0] * side, j[1] + p[1], j[2] + p[2]) for p in path]
            out.append(_part(nm, j, lambda m, pts=pts, radii=radii: limb(m, pts, radii, seg)))
    order = {n: i for i, n in enumerate(LEGS)}
    return sorted(out, key=lambda p: order[p.name])


# ---- the six animals ---------------------------------------------------------------------


def hare() -> Animal:
    """Crouched, ready to spring: heavy haunches, long ears."""

    def body(m):
        sp = curve([(0, 0.17, -0.18), (0, 0.18, -0.08), (0, 0.21, 0.02), (0, 0.25, 0.1)], 10)
        prof = smooth_profile(10, 1.0, bias=0.4)
        loft(m, sp, [(0.12 * r, 0.13 * r) for r in prof], 12)

    def head(m):
        ellipsoid(m, (0, 0.31, 0.14), 0.07, 0.068, 0.085, seg=10, rings=6)
        ellipsoid(m, (0, 0.29, 0.21), 0.04, 0.034, 0.035, seg=8, rings=4)
        for s in (1.0, -1.0):
            flat(m, [(0.03 * s, 0.36, 0.11), (0.045 * s, 0.45, 0.07), (0.055 * s, 0.53, 0.02)],
                 [0.026, 0.033, 0.02], 0.011, seg=6, up=(0.0, 0.0, 1.0))

    def tail(m):
        ellipsoid(m, (0, 0.23, -0.2), 0.038, 0.038, 0.032, seg=8, rings=4)

    legs = _quad_legs(
        (0.045, 0.16, 0.08), (0.07, 0.17, -0.09),
        [(0, 0, 0), (0, -0.08, 0.02), (0, -0.14, 0.03), (0, -0.152, 0.06)],
        [(0, 0, 0), (0.005, -0.08, -0.02), (0.005, -0.148, 0.0), (0.005, -0.155, 0.08)],
        [0.032, 0.02, 0.016, 0.014], [0.065, 0.035, 0.02, 0.016])
    return Animal("hare", "hop", 0.0, [
        _part("body", (0, 0.2, -0.03), body),
        _part("head", (0, 0.27, 0.1), head),
        *legs,
        _part("tail", (0, 0.23, -0.18), tail),
    ])


def fox() -> Animal:
    """Slim and alert: a pointed muzzle, tall ears, a great brush of a tail."""

    def body(m):
        sp = curve([(0, 0.33, -0.3), (0, 0.34, -0.1), (0, 0.35, 0.1), (0, 0.38, 0.27)], 10)
        prof = smooth_profile(10, 1.0, bias=0.55)
        loft(m, sp, [(0.1 * r, 0.115 * r) for r in prof], 12)

    def head(m):
        ellipsoid(m, (0, 0.47, 0.31), 0.085, 0.075, 0.08, seg=10, rings=6)
        loft(m, curve([(0, 0.46, 0.32), (0, 0.45, 0.4), (0, 0.43, 0.46), (0, 0.42, 0.52)], 6),
             [(0, 0), (0.05, 0.045), (0.038, 0.034), (0.026, 0.024), (0.016, 0.015), (0, 0)], 8)
        for s in (1.0, -1.0):
            cone(m, (0.045 * s, 0.52, 0.3), (0.07 * s, 0.64, 0.27), 0.04, seg=6, up=(0, 0, 1), squash=0.45)

    def tail(m):
        loft(m, curve([(0, 0.37, -0.29), (0, 0.31, -0.44), (0, 0.25, -0.6), (0, 0.28, -0.76)], 10),
             [(0, 0), (0.035, 0.035), (0.06, 0.06), (0.08, 0.078), (0.09, 0.087), (0.088, 0.084), (0.078, 0.072),
              (0.06, 0.055), (0.035, 0.032), (0, 0)], 10)

    legs = _quad_legs(
        (0.055, 0.3, 0.19), (0.06, 0.31, -0.2),
        [(0, 0, 0), (0, -0.15, 0.01), (0, -0.27, 0.0), (0, -0.287, 0.03)],
        [(0, 0, 0), (0, -0.13, -0.05), (0, -0.25, -0.02), (0, -0.297, 0.02)],
        [0.045, 0.026, 0.02, 0.018], [0.06, 0.03, 0.02, 0.018])
    return Animal("fox", "trot", 0.0, [
        _part("body", (0, 0.34, 0.0), body),
        _part("head", (0, 0.42, 0.25), head),
        *legs,
        _part("tail", (0, 0.36, -0.28), tail),
    ])


def cat() -> Animal:
    """Neat and round-headed, the tail held up in a hook."""

    def body(m):
        sp = curve([(0, 0.23, -0.2), (0, 0.24, -0.06), (0, 0.245, 0.08), (0, 0.27, 0.19)], 10)
        prof = smooth_profile(10, 1.0, bias=0.45)
        loft(m, sp, [(0.08 * r, 0.085 * r) for r in prof], 12)

    def head(m):
        ellipsoid(m, (0, 0.34, 0.23), 0.08, 0.07, 0.072, seg=10, rings=6)
        ellipsoid(m, (0, 0.315, 0.285), 0.038, 0.028, 0.03, seg=8, rings=4)
        for s in (1.0, -1.0):
            cone(m, (0.045 * s, 0.39, 0.22), (0.06 * s, 0.46, 0.21), 0.03, seg=6, up=(0, 0, 1), squash=0.5)

    def tail(m):
        limb(m, [(0, 0.26, -0.19), (0, 0.28, -0.3), (0, 0.38, -0.38), (0, 0.5, -0.36), (0, 0.56, -0.3)],
             [0.026, 0.022, 0.019, 0.016, 0.013], seg=7)

    legs = _quad_legs(
        (0.042, 0.22, 0.13), (0.045, 0.23, -0.14),
        [(0, 0, 0), (0, -0.11, 0.0), (0, -0.2, 0.0), (0, -0.207, 0.02)],
        [(0, 0, 0), (0, -0.09, -0.03), (0, -0.19, -0.01), (0, -0.217, 0.02)],
        [0.035, 0.02, 0.016, 0.014], [0.045, 0.022, 0.016, 0.014])
    return Animal("cat", "glide", 0.0, [
        _part("body", (0, 0.24, 0.0), body),
        _part("head", (0, 0.29, 0.18), head),
        *legs,
        _part("tail", (0, 0.26, -0.18), tail),
    ])


def owl() -> Animal:
    """In flight: the body leans forward, broad wings spread; it flies at
    the holder's shoulder (`follow_height_m`), origin at its middle."""

    def body(m):
        sp = curve([(0, -0.04, -0.14), (0, -0.01, -0.04), (0, 0.03, 0.06), (0, 0.06, 0.12)], 10)
        prof = smooth_profile(10, 1.0, bias=0.55)
        loft(m, sp, [(0.095 * r, 0.1 * r) for r in prof], 12)

    def head(m):
        ellipsoid(m, (0, 0.11, 0.14), 0.09, 0.08, 0.08, seg=10, rings=6)
        cone(m, (0, 0.095, 0.21), (0, 0.065, 0.245), 0.017, seg=5)
        for s in (1.0, -1.0):
            cone(m, (0.055 * s, 0.17, 0.13), (0.075 * s, 0.22, 0.1), 0.02, seg=5, up=(0, 0, 1), squash=0.5)

    def wing(s):
        def build(m):
            flat(m, [(0.05 * s, 0.03, 0.0), (0.17 * s, 0.06, -0.015), (0.3 * s, 0.06, -0.045), (0.43 * s, 0.04, -0.1)],
                 [0.13, 0.13, 0.1, 0.045], 0.02, seg=8, up=(0.0, 1.0, 0.0))
        return build

    def tail(m):
        flat(m, [(0, -0.03, -0.12), (0, -0.05, -0.2), (0, -0.06, -0.27)], [0.045, 0.07, 0.08], 0.014, seg=6)

    return Animal("owl", "fly", 1.45, [
        _part("body", (0, 0.0, 0.0), body),
        _part("head", (0, 0.07, 0.1), head),
        _part("wing_l", (0.05, 0.03, 0.0), wing(1.0)),
        _part("wing_r", (-0.05, 0.03, 0.0), wing(-1.0)),
        _part("tail", (0, -0.03, -0.12), tail),
    ])


def otter() -> Animal:
    """Long and sleek; it swims through the air at knee height."""

    def body(m):
        sp = curve([(0, 0.0, -0.3), (0, 0.0, -0.12), (0, 0.0, 0.06), (0, 0.02, 0.25)], 10)
        prof = smooth_profile(10, 1.0, bias=0.5)
        prof = [max(r, 0.75) if 0 < i < 9 else 0.0 for i, r in enumerate(prof)]
        loft(m, sp, [(0.08 * r, 0.07 * r) for r in prof], 12)

    def head(m):
        ellipsoid(m, (0, 0.04, 0.32), 0.066, 0.056, 0.075, seg=10, rings=6)
        ellipsoid(m, (0, 0.025, 0.385), 0.038, 0.028, 0.032, seg=8, rings=4)
        for s in (1.0, -1.0):
            ellipsoid(m, (0.052 * s, 0.08, 0.3), 0.016, 0.016, 0.011, seg=6, rings=3)

    def tail(m):
        loft(m, curve([(0, 0.0, -0.28), (0, -0.005, -0.4), (0, -0.01, -0.52), (0, 0.0, -0.64)], 8),
             [(0, 0), (0.05, 0.045), (0.044, 0.038), (0.035, 0.028), (0.026, 0.02), (0.017, 0.013),
              (0.009, 0.007), (0, 0)], 8)

    legs = _quad_legs(
        (0.055, -0.025, 0.17), (0.06, -0.025, -0.17),
        [(0, 0, 0), (0.005, -0.04, 0.01), (0.008, -0.07, 0.03)],
        [(0, 0, 0), (0.005, -0.04, -0.02), (0.01, -0.06, -0.06)],
        [0.026, 0.02, 0.016], [0.03, 0.022, 0.018], seg=6)
    return Animal("otter", "swim", 0.55, [
        _part("body", (0, 0.0, 0.0), body),
        _part("head", (0, 0.03, 0.24), head),
        *legs,
        _part("tail", (0, 0.0, -0.27), tail),
    ])


def horse() -> Animal:
    """A pony-sized horse (about 1.1 m at the withers), mane and flowing tail."""

    def body(m):
        sp = curve([(0, 0.86, -0.52), (0, 0.84, -0.25), (0, 0.85, 0.1), (0, 0.92, 0.42)], 11)
        prof = smooth_profile(11, 1.0, bias=0.5)
        prof = [max(r, 0.65) if 0 < i < 10 else 0.0 for i, r in enumerate(prof)]
        loft(m, sp, [(0.21 * r, 0.25 * r) for r in prof], 14)

    def head(m):
        # Neck rising forward, then the long head down to the muzzle.
        loft(m, curve([(0, 0.88, 0.26), (0, 1.02, 0.4), (0, 1.2, 0.52), (0, 1.36, 0.6)], 8),
             [(0, 0), (0.13, 0.17), (0.12, 0.155), (0.105, 0.135), (0.095, 0.115), (0.088, 0.1), (0.08, 0.09),
              (0, 0)], 10)
        loft(m, curve([(0, 1.44, 0.54), (0, 1.39, 0.66), (0, 1.28, 0.8), (0, 1.18, 0.9)], 8),
             [(0, 0), (0.078, 0.092), (0.082, 0.094), (0.072, 0.083), (0.062, 0.072), (0.058, 0.066),
              (0.052, 0.056), (0, 0)], 10)
        for s in (1.0, -1.0):
            cone(m, (0.045 * s, 1.46, 0.6), (0.06 * s, 1.57, 0.57), 0.03, seg=5, up=(0, 0, 1), squash=0.55)
        # The mane: a blade along the top of the neck.
        flat(m, [(0, 1.46, 0.55), (0, 1.33, 0.48), (0, 1.17, 0.38), (0, 1.03, 0.28)],
             [0.05, 0.07, 0.065, 0.035], 0.018, seg=6, up=(1.0, 0.0, 0.0))

    def tail(m):
        flat(m, [(0, 0.92, -0.52), (0, 0.86, -0.64), (0, 0.7, -0.72), (0, 0.52, -0.74), (0, 0.38, -0.72)],
             [0.04, 0.075, 0.09, 0.075, 0.035], 0.035, seg=7, up=(1.0, 0.0, 0.0))

    legs = _quad_legs(
        (0.11, 0.8, 0.32), (0.11, 0.82, -0.38),
        [(0, 0, 0), (0, -0.27, 0.01), (0, -0.5, 0.0), (0, -0.72, -0.01), (0, -0.765, 0.02)],
        [(0, 0, 0), (0, -0.24, -0.09), (0, -0.44, -0.06), (0, -0.72, -0.02), (0, -0.785, 0.01)],
        [0.09, 0.055, 0.042, 0.04, 0.05], [0.11, 0.065, 0.045, 0.04, 0.05], seg=7)
    return Animal("horse", "gallop", 0.0, [
        _part("body", (0, 0.85, 0.0), body),
        _part("head", (0, 0.95, 0.34), head),
        *legs,
        _part("tail", (0, 0.9, -0.5), tail),
    ])


BUILDERS = {"hare": hare, "fox": fox, "cat": cat, "owl": owl, "otter": otter, "horse": horse}


def animals() -> list[Animal]:
    return [BUILDERS[k]() for k in ANIMALS]


def node_name(animal: str, part: str) -> str:
    return f"guardian_{animal}_{part}"


def manifest(built: list[Animal]) -> dict:
    """What the client reads besides the glb: per animal its parts (node
    names), size (m: x across, y up, z long), gait, follow height and the
    joint pivots (the animal's frame, as the nodes sit)."""
    out = {}
    for a in built:
        out[a.key] = {
            "parts": [p.name for p in a.parts],
            "nodes": {p.name: node_name(a.key, p.name) for p in a.parts},
            "size_m": list(a.size()),
            "gait": a.gait,
            "follow_height_m": a.follow_height_m,
            "pivots": {p.name: [round(c, 4) for c in p.pivot] for p in a.parts},
            "triangles": a.triangles(),
        }
    return {"version": 1, "glb": "guardian.glb", "animals": out}
