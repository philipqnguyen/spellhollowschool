"""Anime hair rebuilt as locks on the body's own head (character adoption).

Each hairstyle of `data/characters/creator.json` (v2) belongs to one body
and is data in `hair_styles.py`: a volume field over the skull and a list of
**locks**. This module turns that data into meshes, per level of detail:

* **Locks**: a closed tapered tube with a crescent section (convex outside,
  edges curling in toward the head) and a pointed tip, along a spine that
  follows the skull at the style's volume (a great-circle walk from its root
  to its on-head end, optionally bowed), then **hangs** from the widest part
  of the skull and **drops** further for long hair, flares or flicks at the
  tip and is pushed out of the body (head, ears unless hidden, neck, torso).
  Locks overlap in layers (inner, outer, top), so the hem is a row of
  distinct pointed locks and no scalp shows between them.
* an **inner cap** on the scalp (covering at least the body's `scalp`
  region, which hair hides) that fills every gap with deep shade;
* the **cap variant** worn under hats: every vertex above the hat band line
  (`hair_frame.band_phi`) is pressed to within `CAP_CLEARANCE` of the skull
  and ponytails move to the nape (styles decide, `cap=True`);
* **LOD0-LOD3** generated from the same locks with fewer section points,
  rings and (LOD2+) without the minor inner locks: the silhouettes stay;
* the **shade layer** `_SHADE` per vertex (docs/art/character-adoption.md
  section 3): x = ambient occlusion (hemisphere rays against the hair and
  the body, 1.5 cm, three quarters of it kept: the shingled locks a few mm
  apart darkened every outer surface to the floor at 3 cm and full weight)
  x the root-to-tip value (0.72 at the root to 1 from a
  third of the length), y = the strand coordinate (the client replaces it with
  the elevation round the head's centre, `assemble.ts` `ringY`), laid out so the shader's
  highlight band (`y` near `BAND_Y`) falls on one latitude of the head on
  every lock (a continuous angel ring), z = the band phase per lock, w = the
  outline hint;
* shading **normals** bent toward a smooth head-shaped proxy (the light and
  shadow shapes read as big anime planes, not per-lock noise);
* **sway chains** (`hair.back.01`-`04`, `hair.side.L/R.01`-`03`,
  `hair.front.01`-`02`): joints fitted to the locks they drive, smooth
  weights (head plus at most two chain bones). In the saved .blend the hair
  is rigid on the head (the sway at rest, which is what the clipping checks
  measure); the sway weights are kept in `sway:<bone>` vertex groups and
  applied only in the exported file, whose skeleton gets the chain joints.

All colour comes from the dye mask (COLOR_0 R = hair colour) on the one
near-white atlas cell `hair`; the angel ring, root darkening and fringe
shadow are computed by the client's shader from `_SHADE` and `fringe_hem`.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import hair_frame as HF

CAP_CLEARANCE = 0.009  # metres above the skull under a hat (hats stand at least 11 mm off)
CAP_MARGIN = 3.0  # degrees below the band line still pressed
BAND_Y = 0.28  # the client's bandCentre (toon palette): y of the angel ring
BAND_SLOPE = 0.075  # y per head centimetre along a lock near the ring
BAND_PHASE = 0.03  # +- band offset between neighbouring locks (_SHADE.z)
OUTLINE_W = 0.45  # _SHADE.w on hair (thinner lines than clothing)
AO_DIST = 0.015
AO_WEIGHT = 0.75  # share of the occlusion kept (shingled locks a few mm apart darkened every outer surface at full weight)
AO_RAYS = 40
SHADE_FLOOR = 0.35
ROOT_VALUE = 0.72
BUDGETS = {  # LOD triangles per length class (character-adoption.md section 4)
    "short": (6000, 2000, 700, 250),
    "medium": (7000, 2400, 800, 250),
    "long": (8000, 2700, 900, 300),
}
LOD_SECTION = (6, 4, 3, 3)  # points round a lock's section
LOD_RINGS = (1.0, 0.5, 0.3, 0.2)  # share of the LOD0 rings
CHAIN_JOINTS = {"back": 4, "side.L": 3, "side.R": 3, "front": 2}
HEAD_BONE = "DEF-spine.006"
FRINGE_SHADOW = {"depth_m": 0.03, "soft_m": 0.012, "strength": 0.55}


# ----------------------------------------------------------------------------
# Style data
# ----------------------------------------------------------------------------


@dataclass
class Lock:
    """One lock. Angles in degrees (head frame), lengths in head cm."""

    root: tuple[float, float]  # (phi, theta) where it grows from
    tip: tuple[float, float]  # (phi, theta) where its on-head part ends
    width: float = 3.0  # widest (hcm)
    thick: float = 0.8  # thickest (hcm)
    lift: tuple[float, float, float] = (0.6, 1.0, 0.8)  # x the style volume at root, middle, end of the on-head part
    bend: float = 0.0  # sideways bow of the on-head walk (degrees of arc at the middle)
    hang: float = 0.0  # 0 = follows the skull, 1 = falls straight from the widest point
    drop: float = 0.0  # extra length hanging below the on-head end (hcm)
    lean: tuple[float, float] = (0.0, 0.0)  # (outward, forward) drift over the drop (hcm)
    flare: float = 0.0  # tip pushed outward (hcm; negative: toward the head)
    curl: float = 0.0  # tip flicks up/out (hcm)
    twist: float = 0.0  # sideways drift of the tip over the drop (hcm, + = toward +theta)
    layer: int = 1  # 0 inner, 1 outer, 2 top (volume multiplier and draw order)
    blunt: bool = False  # flat end that melts into the skin (combed-up hair at the hairline)
    taper: float = 0.5  # share of the length where the point begins (long hair: late)
    roll: float = 0.0  # degrees the section turns about the spine on the hanging part (side locks show their face to the front)
    segs: int = 10  # rings at LOD0
    group: str = ""  # fringe, crown, side, back, tail, frame, nape, tie ...
    chain: str | None = None  # sway chain it hangs from
    lods: int = 2  # the last LOD it is in (1: inner fill, 3: the few locks the farthest LOD keeps)
    path: list | None = None  # explicit world spine (overrides root/tip walk; tails)
    bundle: list | None = None  # with `path`: the bundle's centre line (sections face away from it)
    clearance: float = 0.35  # hcm kept from the skin (plus half the thickness)
    band: str = "ring"  # "ring": y crosses BAND_Y at the ring latitude; "none": never; "s<hcm>": at that arc length


@dataclass
class HairStyle:
    name: str
    body: str
    length: str  # short, medium, long (budget class)
    vol: object  # (phi, theta) arrays -> hcm over the skull
    locks: list[Lock]
    ring_phi: object  # theta -> phi of the angel ring
    cap_hem: object  # theta -> phi the inner cap reaches
    covers_ears: bool = False
    hang_phi: float = 95.0  # polar angle where hanging starts at the latest
    tie: dict | None = None  # ponytail tie {"centre": world point, "axis": vector, "r": metres}
    sheet: dict | None = None  # hair combed to a tie as one grooved sheet (combed_sheet)
    chains: tuple[str, ...] = ()
    widen: float = 1.0  # every lock's width x this (fringe: widen_fringe)
    trunk_clear: float = 0.012  # metres kept from the neck, shoulders and back (long hair: over the robe's collar)
    arm_clear: float = 0.0  # metres kept from the upper arms (long hair: room for the arm swing)
    widen_fringe: float = 1.0


def periodic(ctrl, mirror: bool = True):
    """Smooth periodic curve over azimuth (degrees) through (theta, value)
    points; `mirror` adds the other side (360 - theta)."""
    pts = list(ctrl)
    if mirror:
        pts += [(360.0 - t, v) for t, v in ctrl if 0.0 < t < 180.0]
    pts = sorted(((t % 360.0, v) for t, v in pts))
    th = np.array([p[0] for p in pts])
    vals = np.array([p[1] for p in pts])
    xs = np.concatenate([th - 360.0, th, th + 360.0])
    ys = np.concatenate([vals, vals, vals])
    grid = np.linspace(-360.0, 720.0, 1081)
    dense = np.interp(grid, xs, ys)
    k = 12
    dense = np.convolve(np.pad(dense, k, mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), mode="same")[k:-k]
    return lambda t: np.interp(np.asarray(t, dtype=np.float64) % 360.0, grid, dense)


def volume_field(top: float, rows: list[tuple[float, object]]):
    """Volume over the skull: `rows` = [(phi, theta -> hcm or a number)],
    linearly between rows by phi; `top` above the first row."""
    rows = sorted(rows, key=lambda r: r[0])

    def f(phi, theta):
        phi = np.asarray(phi, dtype=np.float64)
        theta = np.asarray(theta, dtype=np.float64)
        vals = [np.full(np.broadcast(phi, theta).shape, float(top))]
        ps = [0.0]
        for p, v in rows:
            ps.append(p)
            vals.append(np.broadcast_to(v(theta) if callable(v) else np.full(theta.shape, float(v)),
                                        np.broadcast(phi, theta).shape).astype(np.float64))
        out = np.array(vals[-1], dtype=np.float64)
        for k in range(len(ps) - 1, 0, -1):
            a = np.clip((phi - ps[k - 1]) / max(ps[k] - ps[k - 1], 1e-6), 0.0, 1.0)
            seg = vals[k - 1] * (1 - a) + vals[k] * a
            out = np.where(phi <= ps[k], seg, out)
        return out

    return f


# ----------------------------------------------------------------------------
# Geometry
# ----------------------------------------------------------------------------


def _slerp(a: np.ndarray, b: np.ndarray, u: np.ndarray) -> np.ndarray:
    dot = float(np.clip(a @ b, -1.0, 1.0))
    om = math.acos(dot)
    if om < 1e-6:
        return np.outer(1 - u, a) + np.outer(u, b)
    so = math.sin(om)
    return (np.sin((1 - u) * om)[:, None] * a + np.sin(u * om)[:, None] * b) / so


def _resample(P: np.ndarray, n: int, extra: np.ndarray | None = None):
    """`n` points evenly by arc length along a polyline (and the arc length
    of each). `extra` (per point values) is interpolated too."""
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    a = np.concatenate([[0.0], np.cumsum(seg)])
    t = np.linspace(0.0, a[-1], n)
    Q = np.stack([np.interp(t, a, P[:, k]) for k in range(3)], axis=1)
    ex = None
    if extra is not None:
        ex = np.interp(t, a, extra)
    return Q, t, ex


def _smoothstep(e0, e1, x):
    t = np.clip((np.asarray(x, dtype=np.float64) - e0) / (e1 - e0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


class LockPath:
    """The spine of one lock at one LOD: points, arc lengths, the arc length
    where it leaves the head (hanging starts), the ring crossing."""

    def __init__(self, P, s, s_free, s_ring, on_head):
        self.P = P
        self.s = s
        self.L = float(s[-1])
        self.s_free = s_free
        self.s_ring = s_ring
        self.on_head = on_head


def lock_spine(fr: HF.HeadFrame, st: HairStyle, lk: Lock, cap: bool, n: int, collide: BVHTree,
               stack: float = 0.0) -> LockPath:
    h = fr.hcm
    fine = 64
    if lk.path is not None:
        P = np.asarray(lk.path, dtype=np.float64)
        on = np.zeros(len(P), dtype=bool)
        P, s, _ = _resample(P, fine)
        on = np.zeros(fine, dtype=bool)
        s_free = 0.0
    else:
        d0 = fr.dirs(*lk.root)
        d1 = fr.dirs(*lk.tip)
        u = np.linspace(0.0, 1.0, fine)
        D = _slerp(d0, d1, u)
        if lk.bend:
            side = np.cross(d0, d1)
            ln = np.linalg.norm(side)
            side = side / ln if ln > 1e-9 else np.array([1.0, 0.0, 0.0])
            D = D + np.outer(np.sin(np.pi * u) * math.radians(lk.bend), side)
        D /= np.linalg.norm(D, axis=1, keepdims=True)
        x, f, z = D[:, 0], -D[:, 1], D[:, 2]
        phi = np.degrees(np.arccos(np.clip(z, -1, 1)))
        theta = np.degrees(np.arctan2(x, f)) % 360.0
        lift = np.where(u < 0.5, lk.lift[0] + (lk.lift[1] - lk.lift[0]) * _smoothstep(0, 0.5, u),
                        lk.lift[1] + (lk.lift[2] - lk.lift[1]) * _smoothstep(0.5, 1.0, u))
        layer_k = (0.55, 1.0, 1.15)[lk.layer]
        hgt = st.vol(phi, theta) * lift * layer_k * h + stack * _smoothstep(0.0, 0.25, u)
        if cap:
            above = phi < np.array([HF.band_phi(t) for t in theta]) + CAP_MARGIN
            hgt = np.where(above, np.minimum(hgt, CAP_CLEARANCE * 0.85), hgt)
        hgt = np.maximum(hgt, 0.15 * h)
        P = fr.centre + D * (fr.skull_r(phi, theta) + hgt)[:, None]
        on = np.ones(fine, dtype=bool)
        s_free = None
        # Hang: from the widest point (horizontally) the lock falls instead of
        # following the skull inward.
        if lk.hang > 0:
            rad = np.hypot(P[:, 0] - fr.centre[0], P[:, 1] - fr.centre[1])
            cand = np.nonzero((u > 0.15) & (phi > 60.0))[0]
            if len(cand):
                iw = int(cand[np.argmax(rad[cand])]) if phi[-1] > 60 else len(P) - 1
                iw = min(iw, int(np.searchsorted(phi, st.hang_phi)) if phi[-1] > st.hang_phi else iw)
                iw = max(1, min(iw, len(P) - 2))
                seg = np.linalg.norm(np.diff(P[iw:], axis=0), axis=1)
                dist = np.concatenate([[0.0], np.cumsum(seg)])
                out = P[iw] - fr.centre
                out[2] = 0.0
                out /= max(np.linalg.norm(out), 1e-9)
                fall = P[iw] + np.outer(dist, [0.0, 0.0, -1.0]) * 0.998 + np.outer(dist, out) * 0.05
                k = lk.hang * _smoothstep(0.0, 1.0, dist / max(dist[-1] * 0.35, 1e-6))
                P[iw:] = P[iw:] * (1 - k[:, None]) + fall * k[:, None]
                on[iw + 1:] = False
                s_free = float(np.sum(np.linalg.norm(np.diff(P[: iw + 1], axis=0), axis=1)))
        # Drop: further length below the on-head end.
        if lk.drop > 0:
            L = lk.drop * h
            m = max(8, int(L / (1.2 * h)))
            t = np.linspace(0.0, 1.0, m + 1)[1:]
            tan = P[-1] - P[-3]
            tan /= max(np.linalg.norm(tan), 1e-9)
            down = np.array([0.0, 0.0, -1.0])
            out = P[-1] - fr.centre
            out[2] = 0.0
            out /= max(np.linalg.norm(out), 1e-9)
            fwd = np.array([0.0, -1.0, 0.0])
            side = np.cross(down, out)
            q = [P[-1]]
            d = tan
            for k in range(m):
                d = d * 0.72 + down * 0.28
                d /= np.linalg.norm(d)
                q.append(q[-1] + d * (L / m))
            q = np.array(q[1:])
            # Outward lean grows along the drop; inward (negative) curls the ends under.
            # Outward lean opens early; inward lean (bob ends, long hair
            # narrowing behind the back) closes late, so the hair clears the back.
            lean_t = np.sin(t * np.pi / 2) if lk.lean[0] >= 0 else t ** 2
            q += np.outer(lean_t * lk.lean[0] * h, out) + np.outer(t * lk.lean[1] * h, fwd) \
                + np.outer(t ** 1.5 * lk.twist * h, side)
            if s_free is None:
                s_free = float(np.sum(np.linalg.norm(np.diff(P, axis=0), axis=1)))
            P = np.vstack([P, q])
            on = np.concatenate([on, np.zeros(m, dtype=bool)])
        if s_free is None:
            s_free = float(np.sum(np.linalg.norm(np.diff(P, axis=0), axis=1)))
        # Ring crossing (arc length where the walk passes the ring latitude).
    seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
    a = np.concatenate([[0.0], np.cumsum(seg)])
    # Tip flare / curl over the last 35 %.
    w = np.clip((a / a[-1] - 0.62) / 0.38, 0.0, 1.0) ** 2
    if lk.flare or lk.curl:
        out = P - fr.centre
        if lk.path is not None or not on[-1]:
            out[:, 2] *= 0.25
        out /= np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)
        P = P + out * (w * lk.flare * h)[:, None] + np.outer(w * lk.curl * h, [0.0, 0.0, 1.0]) \
            + out * (w * abs(lk.curl) * 0.5 * h)[:, None]
    s_ring = None
    if lk.band == "ring" and lk.path is None:
        ph, th, _ = fr.polar(P)
        rp = st.ring_phi(th)
        dif = ph - rp
        idx = np.nonzero((dif[:-1] <= 0) & (dif[1:] > 0))[0]
        if not len(idx):
            idx = np.nonzero((dif[:-1] >= 0) & (dif[1:] < 0))[0]
        if len(idx):
            i = int(idx[0])
            t = dif[i] / (dif[i] - dif[i + 1])
            s_ring = float(a[i] + t * (a[i + 1] - a[i]))
    elif lk.band.startswith("s"):
        s_ring = float(lk.band[1:]) * h
    # Even rings, then collisions (a few rounds with light smoothing).
    Q, s, onf = _resample(P, n, on.astype(np.float64))
    clr = (lk.clearance + 0.5 * lk.thick) * h
    trunk = st.trunk_clear + 0.5 * lk.thick * h
    for it in range(3):
        for i in range(1, n):
            c = clr * (0.6 if i == 1 else 1.0)
            if Q[i][2] < fr.chin_z + 0.01:
                c = max(c, trunk)
            Q[i] = fr.clear(Q[i], c, collide)
            if st.arm_clear and fr.arms is not None:
                Q[i] = fr.clear(Q[i], st.arm_clear + 0.5 * lk.thick * h, fr.arms, reach=0.1)
        if it < 2:
            Q[1:-1] = Q[1:-1] * 0.6 + 0.2 * (Q[:-2] + Q[2:])
    seg = np.linalg.norm(np.diff(Q, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    return LockPath(Q, s, min(s_free, s[-1]), s_ring, onf > 0.5)


SECTIONS = {
    6: [(1.0, -0.35), (0.55, 0.75), (-0.55, 0.75), (-1.0, -0.35), (-0.5, -0.55), (0.5, -0.55)],
    4: [(1.0, -0.3), (0.0, 0.9), (-1.0, -0.3), (0.0, -0.55)],
    3: [(1.0, -0.25), (0.0, 0.85), (-1.0, -0.25)],
}


def width_profile(u: np.ndarray, blunt: bool, taper: float = 0.5) -> np.ndarray:
    grow = 0.72 + 0.28 * _smoothstep(0.0, 0.2, u)
    if blunt:
        return grow * (1.0 - 0.15 * u)
    return grow * (1.0 - _smoothstep(taper, 1.0, u) ** 1.1)


def thick_profile(u: np.ndarray, blunt: bool) -> np.ndarray:
    if blunt:
        return (1.0 - 0.9 * _smoothstep(0.6, 1.0, u)) * (0.8 + 0.2 * _smoothstep(0, 0.2, u))
    return (1.0 - 0.75 * u) * (0.75 + 0.25 * _smoothstep(0, 0.2, u))


@dataclass
class Geo:
    """Hair geometry of one LOD before it becomes a Blender mesh."""

    verts: list = field(default_factory=list)
    faces: list = field(default_factory=list)
    face_kind: list = field(default_factory=list)  # "hair" | "tie"
    lock: list = field(default_factory=list)  # per vertex: lock index (-1 cap, -2 tie)
    s: list = field(default_factory=list)  # arc length from the root (m)
    L: list = field(default_factory=list)  # lock length (m)
    s_ring: list = field(default_factory=list)  # ring crossing of the lock (m) or nan
    top: list = field(default_factory=list)  # 1 on the outer surface, 0 underneath, 0.5 edges
    s_free: list = field(default_factory=list)

    def add(self, p, lock, s, L, s_ring, top, s_free=0.0) -> int:
        self.verts.append(np.asarray(p, dtype=np.float64))
        self.lock.append(lock)
        self.s.append(s)
        self.L.append(L)
        self.s_ring.append(np.nan if s_ring is None else s_ring)
        self.top.append(top)
        self.s_free.append(s_free)
        return len(self.verts) - 1

    def tris(self) -> int:
        return sum(len(f) - 2 for f in self.faces)


def sweep_lock(g: Geo, fr: HF.HeadFrame, lk: Lock, idx: int, path: LockPath, K: int, widen: float = 1.0) -> None:
    P = path.P
    n = len(P)
    u = path.s / max(path.L, 1e-9)
    wid = width_profile(u, lk.blunt, lk.taper) * lk.width * widen * fr.hcm
    thk = thick_profile(u, lk.blunt) * lk.thick * fr.hcm
    sec = SECTIONS[K]
    rings = []
    prev_side = None
    for i in range(n - (0 if lk.blunt else 1)):
        tan = P[min(i + 1, n - 1)] - P[max(i - 1, 0)]
        tan /= max(np.linalg.norm(tan), 1e-9)
        if lk.bundle is not None:
            B = np.asarray(lk.bundle)
            j = int(np.argmin(np.linalg.norm(B - P[i], axis=1)))
            out = P[i] - B[j]
            if np.linalg.norm(out) < 1e-5:
                out = P[i] - fr.centre
        elif path.on_head[i] or lk.path is not None and i == 0:
            out = P[i] - fr.centre
        else:
            out = P[i] - fr.centre
            out[2] *= 0.15
        out = out - tan * float(out @ tan)
        out /= max(np.linalg.norm(out), 1e-9)
        side = np.cross(tan, out)
        side /= max(np.linalg.norm(side), 1e-9)
        if lk.roll and not path.on_head[i]:
            k = min(1.0, (path.s[i] - path.s_free) / (3.0 * fr.hcm)) if path.s[i] > path.s_free else 0.0
            a = math.radians(lk.roll) * k
            side, out = side * math.cos(a) - out * math.sin(a), out * math.cos(a) + side * math.sin(a)
        if prev_side is not None and float(side @ prev_side) < 0:
            side = -side
            out = -out
        prev_side = side
        ring = []
        for a, b in sec:
            p = P[i] + side * (a * 0.5 * wid[i]) + out * (b * 0.5 * thk[i])
            ring.append(g.add(p, idx, float(path.s[i]), path.L, path.s_ring, 1.0 if b > 0.1 else (0.0 if b < -0.1 else 0.5),
                              path.s_free))
        rings.append(ring)
    for r0, r1 in zip(rings, rings[1:]):
        for j in range(K):
            j1 = (j + 1) % K
            g.faces.append((r0[j], r1[j], r1[j1], r0[j1]))
            g.face_kind.append("hair")
    if not lk.blunt:
        tip = g.add(P[-1], idx, float(path.s[-1]), path.L, path.s_ring, 0.5, path.s_free)
        last = rings[-1]
        for j in range(K):
            g.faces.append((last[j], tip, last[(j + 1) % K]))
            g.face_kind.append("hair")
    else:
        last = rings[-1]
        g.faces.append(tuple(last[::-1]) if K > 3 else tuple(last[::-1]))
        g.face_kind.append("hair")
    first = rings[0]
    g.faces.append(tuple(first[::-1]))
    g.face_kind.append("hair")


def inner_cap(g: Geo, fr: HF.HeadFrame, st: HairStyle, lod: int, cap: bool, lift: float = 0.4) -> None:
    """A shell on the scalp under every lock (gaps show deep shade, never
    skin or the hidden scalp). At LOD2-3, with few locks left, it swells to
    the style's volume and carries the hair's mass."""
    rows = (12, 7, 4, 3)[lod]
    cols = (36, 20, 10, 8)[lod]
    th = np.arange(cols) * 360.0 / cols
    hem = np.maximum(st.cap_hem(th), fr.scalp_border(th) + 3.0)
    mass = (0.0, 0.0, 0.7, 0.9)[lod]

    def hgt(ph, f):
        base = lift * fr.hcm + mass * st.vol(ph, th) * fr.hcm
        h = base * (1.0 - 0.75 * f ** 3)
        if cap:
            h = np.minimum(h, CAP_CLEARANCE * 0.7)
        return h

    pole = g.add(fr.point(0.0, 0.0, float(hgt(np.zeros(cols), 0.0)[0])), -1, 0.0, 1.0, None, 1.0)
    ring_ids = []
    for r in range(1, rows + 1):
        f = r / rows
        ph = hem * f
        pts = fr.point(ph, th, hgt(ph, f))
        ring_ids.append([g.add(p, -1, 0.0, 1.0, None, 1.0) for p in pts])
    for c in range(cols):
        g.faces.append((pole, ring_ids[0][c], ring_ids[0][(c + 1) % cols]))
        g.face_kind.append("hair")
    for r0, r1 in zip(ring_ids, ring_ids[1:]):
        for c in range(cols):
            c1 = (c + 1) % cols
            g.faces.append((r0[c], r1[c], r1[c1], r0[c1]))
            g.face_kind.append("hair")


SHEET = -3  # lock index of the combed sheet's vertices


def combed_sheet(g: Geo, fr: HF.HeadFrame, st: HairStyle, lod: int, cap: bool) -> None:
    """Hair combed from the hairline to a ponytail's tie as one sheet with
    clump ridges and grooves running to the tie (smooth, never a tangle of
    crossing locks). `st.sheet`: {"hem": theta -> phi, "tie": world point,
    "ring": metres, "ridges": n, "vol": hcm, "depth": groove share}."""
    sp = st.sheet
    cols = (60, 32, 16, 10)[lod]
    rows = (12, 7, 4, 3)[lod]
    h = fr.hcm
    tie = np.asarray(sp["tie"], dtype=np.float64)
    tp, tt, _ = fr.polar(tie[None])
    d_tie = fr.dirs(float(tp[0]), float(tt[0]))
    th = np.arange(cols) * 360.0 / cols
    hem = np.maximum(sp["hem"](th), fr.scalp_border(th) + 3.0)  # always over the whole hidden scalp
    ridges = sp.get("ridges", 12)
    depth = sp.get("depth", 0.35)
    grid = []
    svals = []
    for c in range(cols):
        d0 = fr.dirs(float(hem[c]), float(th[c]))
        u = np.linspace(0.0, 1.0, rows + 1)
        D = _slerp(d0, d_tie, u)
        D /= np.linalg.norm(D, axis=1, keepdims=True)
        x, f, z = D[:, 0], -D[:, 1], D[:, 2]
        phi = np.degrees(np.arccos(np.clip(z, -1, 1)))
        theta = np.degrees(np.arctan2(x, f)) % 360.0
        groove = 1.0 - depth * (0.5 - 0.5 * math.cos(2 * math.pi * ridges * c / cols))
        hgt = sp["vol"] * h * groove * _smoothstep(0.0, 0.3, u) + 0.6 * h
        if cap:
            above = phi < np.array([HF.band_phi(t) for t in theta]) + CAP_MARGIN
            hgt = np.where(above, np.minimum(hgt, CAP_CLEARANCE * 0.7), hgt)
        P = fr.centre + D * (fr.skull_r(phi, theta) + hgt)[:, None]
        # Into a ring round the tie at the end.
        side = np.cross(d_tie, [0.0, 0.0, 1.0])
        side = side / max(np.linalg.norm(side), 1e-9)
        up = np.cross(side, d_tie)
        a = 2 * math.pi * c / cols
        ring_pt = tie + (side * math.sin(a) + up * math.cos(a)) * sp["ring"]
        k = _smoothstep(0.7, 1.0, u)
        P = P * (1 - k[:, None]) + ring_pt[None] * k[:, None]
        grid.append(P)
        seg = np.linalg.norm(np.diff(P[::-1], axis=0), axis=1)
        svals.append(np.concatenate([[0.0], np.cumsum(seg)])[::-1])  # from the tie
    ids = []
    for c in range(cols):
        P, sv = grid[c], svals[c]
        L = float(sv.max())
        ph, tht, _ = fr.polar(P)
        dif = ph - st.ring_phi(tht)
        # Ring crossing measured from the tie.
        s_ring = None
        order = np.argsort(sv)
        dd, ss = dif[order], sv[order]
        for i in range(len(dd) - 1):
            if dd[i] <= 0 < dd[i + 1] or dd[i] >= 0 > dd[i + 1]:
                t = dd[i] / (dd[i] - dd[i + 1])
                s_ring = float(ss[i] + t * (ss[i + 1] - ss[i]))
                break
        ids.append([g.add(P[r], SHEET, float(sv[r]), L, s_ring, 1.0) for r in range(rows + 1)])
    for c in range(cols):
        c1 = (c + 1) % cols
        for r in range(rows):
            g.faces.append((ids[c][r], ids[c1][r], ids[c1][r + 1], ids[c][r + 1]))
            g.face_kind.append("hair")
    # Close the ring round the tie (no hole to the scalp under it).
    end = g.add(tie, SHEET, 0.0, max(float(np.max(svals[0])), 1e-6), None, 1.0)
    for c in range(cols):
        g.faces.append((ids[c][rows], ids[(c + 1) % cols][rows], end))
        g.face_kind.append("hair")


def hair_tie(g: Geo, fr: HF.HeadFrame, tie: dict, lod: int) -> None:
    """The ponytail's tie: a short dark band round the gathered hair."""
    n = (10, 8, 6, 4)[lod]
    c = np.asarray(tie["centre"], dtype=np.float64)
    ax = np.asarray(tie["axis"], dtype=np.float64)
    ax /= np.linalg.norm(ax)
    e1 = np.cross(ax, [1.0, 0.0, 0.0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(ax, e1)
    r = tie["r"]
    hw = tie.get("half_width", 0.006)
    rings = []
    for off, rr in ((-hw, r), (hw, r)):
        ring = []
        for k in range(n):
            a = 2 * math.pi * k / n
            ring.append(g.add(c + ax * off + (e1 * math.cos(a) + e2 * math.sin(a)) * rr, -2, 0.0, 1.0, None, 1.0))
        rings.append(ring)
    for k in range(n):
        k1 = (k + 1) % n
        g.faces.append((rings[0][k], rings[0][k1], rings[1][k1], rings[1][k]))
        g.face_kind.append("tie")


def build_geo(fr: HF.HeadFrame, st: HairStyle, lod: int, cap: bool, rings: float | None = None,
              inner_rings: float | None = None) -> tuple[Geo, list]:
    g = Geo()
    collide = fr.collide_noears if st.covers_ears else fr.collide
    K = LOD_SECTION[lod]
    share = LOD_RINGS[lod] if rings is None else rings
    inner_share = share if inner_rings is None else min(share, inner_rings)
    paths: list = [None] * len(st.locks)
    parity: dict = {}
    for i, lk in enumerate(st.locks):
        # Neighbouring locks of a group alternate a few millimetres in height,
        # so they overlap like shingles instead of cutting through each other.
        k = parity.get((lk.group, lk.layer), 0)
        parity[(lk.group, lk.layer)] = k + 1
        if lod > lk.lods:
            continue
        n = max(3, int(round(lk.segs * (inner_share if lk.layer == 0 else share))) + 1)
        path = lock_spine(fr, st, lk, cap, n, collide, stack=(k % 2) * 0.32 * fr.hcm)
        sweep_lock(g, fr, lk, i, path, K, st.widen_fringe if lk.group == "fringe" else st.widen)
        paths[i] = path
    inner_cap(g, fr, st, lod, cap)
    if st.sheet:
        combed_sheet(g, fr, st, lod, cap)
    if st.tie:
        hair_tie(g, fr, st.tie, lod)
    return g, paths


def build_lod(fr: HF.HeadFrame, st: HairStyle, lod: int, cap: bool) -> tuple[Geo, list]:
    """One LOD within its budget: fewer rings until it fits."""
    budget = BUDGETS[st.length][lod]
    share = LOD_RINGS[lod]
    inner = share
    while True:
        g, paths = build_geo(fr, st, lod, cap, share, inner)
        if g.tris() <= budget or share <= 0.12:
            break
        # The hidden inner fill gives up its rings first, then every lock.
        if inner > 0.45 * share:
            inner *= 0.8
        else:
            share *= 0.9
    if g.tris() > budget:
        raise ValueError(f"{st.name} lod{lod}: {g.tris()} triangles over the {budget} budget")
    # Well under budget (short styles, sheets): smoother locks with the rest.
    while lod <= 1 and share >= LOD_RINGS[lod] and g.tris() < 0.85 * budget and share < 1.6 * LOD_RINGS[lod]:
        g2, p2 = build_geo(fr, st, lod, cap, share * 1.15, inner)
        if g2.tris() > budget:
            break
        share *= 1.15
        g, paths = g2, p2
    return g, paths


# ----------------------------------------------------------------------------
# Cap variant, shade, normals, weights
# ----------------------------------------------------------------------------


def press_cap(fr: HF.HeadFrame, V: np.ndarray, clearance: float = CAP_CLEARANCE) -> np.ndarray:
    """Every vertex above the hat band line within `clearance` of the skull."""
    phi, theta, r = fr.polar(V)
    band = np.array([HF.band_phi(t) for t in theta]) + CAP_MARGIN
    rs = fr.skull_r(phi, theta)
    over = (phi < band) & (r > rs + clearance)
    out = V.copy()
    d = (V - fr.centre) / np.maximum(r[:, None], 1e-12)
    out[over] = fr.centre + d[over] * (rs[over] + clearance)[:, None]
    # Never pressed into the ear (its top reaches the band line on some
    # heads): those few vertices stay where they were, below any brim.
    if fr.ears is not None:
        for i in np.nonzero(over)[0]:
            if fr.inside(out[i], fr.collide, reach=0.03) > 0.0 or fr.depth(out[i], fr.check_tree, reach=0.05) < 0.0025:
                out[i] = V[i]
    return out


def _fib_dirs(n: int) -> np.ndarray:
    i = np.arange(n) + 0.5
    z = 1 - 2 * i / n
    a = math.pi * (3 - math.sqrt(5)) * i
    r = np.sqrt(1 - z * z)
    return np.stack([r * np.cos(a), r * np.sin(a), z], axis=1)


def vertex_normals(V: np.ndarray, faces) -> np.ndarray:
    N = np.zeros_like(V)
    for f in faces:
        p = V[list(f)]
        fn = np.zeros(3)
        for i in range(len(f)):
            fn += np.cross(p[i], p[(i + 1) % len(f)])
        N[list(f)] += fn
    return N / np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)


def adjacency(nv: int, faces) -> list[list[int]]:
    adj = [set() for _ in range(nv)]
    for f in faces:
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            adj[a].add(b)
            adj[b].add(a)
    return [sorted(s) for s in adj]


def ambient_occlusion(V: np.ndarray, N: np.ndarray, occluders: BVHTree) -> np.ndarray:
    dirs = _fib_dirs(AO_RAYS * 2)
    ao = np.ones(len(V))
    for i in range(len(V)):
        n = N[i]
        cos = dirs @ n
        sel = cos > 0.05
        tot = float(cos[sel].sum())
        hit = 0.0
        o = Vector(tuple(V[i] + n * 0.0008))
        for d, c in zip(dirs[sel], cos[sel]):
            if occluders.ray_cast(o, Vector(tuple(d)), AO_DIST)[0] is not None:
                hit += c
        ao[i] = 1.0 - hit / max(tot, 1e-9)
    return ao


def smooth(values: np.ndarray, adj, passes: int = 2) -> np.ndarray:
    v = values.copy()
    for _ in range(passes):
        nv = v.copy()
        for i, nb in enumerate(adj):
            if nb:
                nv[i] = 0.5 * v[i] + 0.5 * float(np.mean(v[nb]))
        v = nv
    return v


def proxy_normals(fr: HF.HeadFrame, V: np.ndarray) -> np.ndarray:
    """A smooth head-shaped field: spherical round the head centre above
    it, horizontal below (hanging hair), squashed a little vertically."""
    c = np.tile(fr.centre, (len(V), 1))
    c[:, 2] = np.minimum(V[:, 2], fr.centre[2])
    d = V - c
    d[:, 2] *= 1.25
    return d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-12)


def band_y(g: Geo, h: float) -> np.ndarray:
    """Strand coordinate: BAND_Y at the ring crossing, BAND_SLOPE per head
    cm near it, then on to 1 at the tip; locks that never cross the ring
    start past the band (no highlight)."""
    s = np.array(g.s)
    L = np.maximum(np.array(g.L), 1e-9)
    sr = np.array(g.s_ring)
    y = np.empty(len(s))
    k = BAND_SLOPE / h
    has = ~np.isnan(sr)
    # Near the ring: linear; then a smooth ramp to 1 at the tip.
    y_near = BAND_Y + (s - np.where(has, sr, 0.0)) * k
    y1 = BAND_Y + 2.0 * h * k  # 2 hcm past the ring
    past = np.where(has, s - sr - 2.0 * h, 0.0)
    rem = np.maximum(L - np.where(has, sr, 0.0) - 2.0 * h, 1e-6)
    y_far = y1 + (1.0 - y1) * np.clip(past / rem, 0.0, 1.0)
    y_ring = np.where(s - np.where(has, sr, 0.0) <= 2.0 * h, np.maximum(y_near, 0.0), y_far)
    y_none = 0.5 + 0.5 * (s / L)
    y[:] = np.where(has, y_ring, y_none)
    lock = np.array(g.lock)
    y[(lock == -1) | (lock == -2)] = 1.0
    return np.clip(y, 0.0, 1.0)


def shade_layer(fr: HF.HeadFrame, g: Geo, V: np.ndarray, N: np.ndarray, occ: BVHTree, adj) -> np.ndarray:
    ao = smooth(ambient_occlusion(V, N, occ), adj, 2)
    s = np.array(g.s)
    L = np.maximum(np.array(g.L), 1e-9)
    lock = np.array(g.lock)
    root = ROOT_VALUE + (1.0 - ROOT_VALUE) * _smoothstep(0.0, 1.0 / 3.0, s / L)
    sheet = lock == SHEET
    root[sheet] = ROOT_VALUE + (1.0 - ROOT_VALUE) * _smoothstep(0.0, 1.0 / 3.0, (L[sheet] - s[sheet]) / L[sheet])
    root[(lock == -1) | (lock == -2)] = ROOT_VALUE
    x = np.clip((1.0 - AO_WEIGHT + AO_WEIGHT * np.power(np.clip(ao, 0, 1), 0.8)) * root, SHADE_FLOOR, 1.0)
    x[lock == -1] = np.minimum(x[lock == -1], 0.5)
    y = band_y(g, fr.hcm)
    z = np.full(len(V), 0.5)
    for i in np.unique(lock[lock >= 0]):
        # Neighbouring locks alternate (a fixed hash, so builds repeat).
        z[lock == i] = 0.5 + BAND_PHASE * math.sin(i * 2.399 + 0.7)
    w = np.full(len(V), OUTLINE_W)
    return np.stack([x, y, z, w], axis=1)


def final_normals(fr: HF.HeadFrame, g: Geo, V: np.ndarray) -> np.ndarray:
    geo = vertex_normals(V, g.faces)
    prox = proxy_normals(fr, V)
    top = np.array(g.top)[:, None]
    # Outer surface and edges: mostly the proxy (big clean light shapes, no
    # rim on every lock edge); undersides keep more of their own.
    k = 0.62 + 0.18 * top
    n = geo * (1 - k) + prox * k
    return n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-12)


# ----------------------------------------------------------------------------
# Sway chains
# ----------------------------------------------------------------------------


def chain_joints(st: HairStyle, paths: list[LockPath | None]) -> dict[str, np.ndarray]:
    """Rest joint positions of every chain: the mean over its locks of the
    free (hanging) part, resampled into the chain's bones."""
    out = {}
    for name in st.chains:
        n = CHAIN_JOINTS[name.split(".")[0] if name.startswith(("back", "front")) else name]
        pts = []
        for lk, p in zip(st.locks, paths):
            if p is None or lk.chain != name:
                continue
            free = max(p.L - p.s_free, 1e-6)
            t = p.s_free + np.linspace(0.0, 1.0, n + 1) * free
            pts.append(np.stack([np.interp(t, p.s, p.P[:, k]) for k in range(3)], axis=1))
        if pts:
            out[name] = np.mean(pts, axis=0)
    return out


def chain_bone_names(name: str) -> list[str]:
    n = CHAIN_JOINTS[name.split(".")[0] if name.startswith(("back", "front")) else name]
    return [f"hair.{name}.{k + 1:02d}" for k in range(n)]


def sway_weights(st: HairStyle, g: Geo) -> list[dict[str, float]]:
    """Per vertex {bone: weight}: the head near the scalp; down a chain a
    hat function over its bones blended with the head for the first half
    bone (at most head + 2 chain bones)."""
    out = []
    s = np.array(g.s)
    L = np.array(g.L)
    sf = np.array(g.s_free)
    for i, li in enumerate(g.lock):
        if li < 0 or st.locks[li].chain is None:
            out.append({HEAD_BONE: 1.0})
            continue
        name = st.locks[li].chain
        bones = chain_bone_names(name)
        nb = len(bones)
        free = max(L[i] - sf[i], 1e-6)
        sig = (s[i] - sf[i]) / free * nb
        if sig <= 0:
            out.append({HEAD_BONE: 1.0})
            continue
        w = {}
        for k in range(nb):
            v = max(0.0, 1.0 - abs(sig - (k + 0.5)))
            if k == nb - 1 and sig > nb - 0.5:
                v = 1.0
            if k == 0 and sig < 0.5:
                v = sig / 0.5
            if v > 1e-4:
                w[bones[k]] = v
        if sig < 0.5:
            w[HEAD_BONE] = 1.0 - sig / 0.5
        tot = sum(w.values())
        top = sorted(w.items(), key=lambda kv: -kv[1])[:3]
        tot = sum(v for _, v in top)
        out.append({k: v / tot for k, v in top})
    return out


# ----------------------------------------------------------------------------
# Meshes and files
# ----------------------------------------------------------------------------


@dataclass
class HairLod:
    V: np.ndarray
    faces: list
    kinds: list
    normals: np.ndarray
    shade: np.ndarray
    weights: list
    geo: Geo
    paths: list


def clear_vertices(fr: HF.HeadFrame, st: HairStyle, g: Geo, V: np.ndarray) -> np.ndarray:
    """Every lock vertex (not only the spines) out of the skin: 3 mm on the
    head and face, `trunk_clear` over the neck, shoulders and back."""
    trees = [t for r, t in fr.region_trees.items() if not (st.covers_ears and r == "ears")]
    # The merged tree too: the clipping checks ask it, and along a thin part's
    # rim (the ear) it can answer with another face than the region tree.
    trees.append(fr.collide_noears if st.covers_ears else fr.collide)
    if not st.covers_ears:
        trees.append(fr.check_tree)
    lock = np.array(g.lock)
    out = V.copy()
    for _ in range(3):
        moved = False
        for i in np.nonzero(lock != -2)[0]:
            p = out[i]
            c = 0.003 if p[2] > fr.chin_z + 0.01 else st.trunk_clear * 0.8
            for tree in trees:
                loc, nrm, _, dist = tree.find_nearest(Vector(tuple(p)), 0.06)
                if loc is None:
                    continue
                hgt = float((p - np.array(loc)) @ np.array(nrm))
                if hgt < c:
                    out[i] = p = p + np.array(nrm) * (c - hgt)
                    moved = True
            if st.arm_clear and fr.arms is not None:
                q = fr.clear(p, st.arm_clear * 0.8, fr.arms, reach=0.08)
                if np.any(q != p):
                    out[i] = q
                    moved = True
        if not moved:
            break
    return out


def finish_lod(fr: HF.HeadFrame, st: HairStyle, g: Geo, paths, cap: bool, occ_extra: list) -> HairLod:
    V = clear_vertices(fr, st, g, np.array(g.verts))
    if cap:
        V = press_cap(fr, V)
    N = final_normals(fr, g, V)
    geoN = vertex_normals(V, g.faces)
    tris = []
    for f in g.faces:
        for i in range(1, len(f) - 1):
            tris.append((f[0], f[i], f[i + 1]))
    occ_V = [V]
    occ_T = [np.array(tris)]
    off = len(V)
    for co, t in occ_extra:
        occ_V.append(co)
        occ_T.append(t + off)
        off += len(co)
    occ = BVHTree.FromPolygons(np.vstack(occ_V).tolist(), np.vstack(occ_T).tolist(), all_triangles=True)
    adj = adjacency(len(V), g.faces)
    shade = shade_layer(fr, g, V, geoN, occ, adj)
    return HairLod(V, list(g.faces), list(g.face_kind), N, shade, sway_weights(st, g), g, paths)


def fringe_hem(fr: HF.HeadFrame, st: HairStyle, lod0: HairLod) -> list[list[float]]:
    """The fringe's lower edge seen from the front: per x bin the lowest
    vertex of a fringe lock in front of the face, in glTF axes (+Y up, +Z
    forward), sorted by x."""
    lock = np.array(lod0.geo.lock)
    fr_ids = [i for i, lk in enumerate(st.locks) if lk.group == "fringe"]
    sel = np.isin(lock, fr_ids)
    V = lod0.V[sel]
    if not len(V):
        return []
    front = V[:, 1] < fr.centre[1] - 3.0 * fr.hcm
    V = V[front]
    out = []
    xs = np.linspace(-6.5, 6.5, 17) * fr.hcm
    half = (xs[1] - xs[0]) * 0.5
    for x in xs:
        m = np.abs(V[:, 0] - x) < half
        if not m.any():
            continue
        p = V[m][np.argmin(V[m][:, 2])]
        out.append([round(float(p[0]), 4), round(float(p[2]), 4), round(float(-p[1]), 4)])
    return out


def to_object(name: str, lod: HairLod, material, coll, atlas) -> bpy.types.Object:
    from .meshutil import MeshData
    from . import meshutil as MU

    md = MeshData()
    # The .blend binding: the sway chains at rest are rigid on the head, so
    # their share goes to the head bone; body bones (hair resting on the
    # chest and shoulders, hair_meshy.body_follow) are bound as they are.
    rigid = []
    for w in lod.weights:
        body = {b: v for b, v in w.items() if b != HEAD_BONE and not b.startswith("hair.")}
        rest = 1.0 - sum(body.values())
        rigid.append({**body, HEAD_BONE: rest} if rest > 1e-6 else body)
    md.add_verts(lod.V, rigid)
    for f, k in zip(lod.faces, lod.kinds):
        if k == "tie":
            md.add_face(f, "hair", "inner_dark", (0.0, 0.0, 0.0))
        else:
            md.add_face(f, "hair", "hair", (1.0, 0.0, 0.0))
    obj = MU.to_object(name, md, atlas, material, collection=coll)
    me = obj.data
    me.normals_split_custom_set_from_vertices([tuple(float(c) for c in n) for n in lod.normals])
    a = me.attributes.new("_SHADE", "FLOAT_COLOR", "POINT")
    a.data.foreach_set("color", lod.shade.astype(np.float32).ravel())
    me.color_attributes.active_color = me.color_attributes["Color"]
    # The sway weights, kept aside (the .blend hair is rigid on the head).
    groups = {}
    for vi, w in enumerate(lod.weights):
        for bone, v in w.items():
            if bone == HEAD_BONE or not bone.startswith("hair."):
                continue
            gname = "sway:" + bone
            gr = groups.get(gname) or obj.vertex_groups.new(name=gname)
            groups[gname] = gr
            gr.add([vi], float(v), "REPLACE")
    hw = obj.vertex_groups.get("sway:" + HEAD_BONE) or obj.vertex_groups.new(name="sway:" + HEAD_BONE)
    for vi, w in enumerate(lod.weights):
        if HEAD_BONE in w:
            hw.add([vi], float(w[HEAD_BONE]), "REPLACE")
    return obj


def export_hair_glb(path: str, skel, objs, chains: dict[str, np.ndarray]) -> None:
    """Export with the chain joints appended to a copy of the skeleton and
    the sway weights applied; the .blend objects stay rigid on the head."""
    from . import export as EX

    os.makedirs(os.path.dirname(path), exist_ok=True)
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    name = skel.name
    dup = skel.copy()
    dup.data = skel.data.copy()
    dup.animation_data_clear()
    for c in skel.users_collection:
        c.objects.link(dup)
    skel.name = name + "__orig"
    dup.name = name
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = dup
    dup.hide_set(False)
    dup.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    eb = dup.data.edit_bones
    for cname, J in sorted(chains.items()):
        parent = eb[HEAD_BONE]
        for k, bname in enumerate(chain_bone_names(cname)):
            b = eb.new(bname)
            b.head = Vector(tuple(J[k]))
            b.tail = Vector(tuple(J[k + 1]))
            if (b.tail - b.head).length < 1e-4:
                b.tail = b.head + Vector((0.0, 0.0, -0.01))
            b.parent = parent
            b.use_deform = True
            parent = b
    bpy.ops.object.mode_set(mode="OBJECT")
    # --- Outlines: the inverted-hull outline layer (charlib/outline_bake.py) ---
    from . import outline_bake as OB

    OB.bake(objs)
    # --- end Outlines ---
    tmp = []
    try:
        for o in objs:
            c = o.copy()
            c.data = o.data.copy()
            for col in o.users_collection:
                col.objects.link(c)
            for gname in [vg.name for vg in c.vertex_groups]:
                vg = c.vertex_groups[gname]
                if gname == HEAD_BONE:
                    c.vertex_groups.remove(vg)
            for vg in list(c.vertex_groups):
                if vg.name.startswith("sway:"):
                    vg.name = vg.name[5:]
            c.parent = dup
            for m in c.modifiers:
                if m.type == "ARMATURE":
                    m.object = dup
            c.name = o.name + "__x"
            tmp.append((c, o.name))
        # Export names must be the originals: swap names for the export.
        for c, oname in tmp:
            o = bpy.data.objects[oname]
            o.name = oname + "__b"
            o.data.name = oname + "__b"
            c.name = oname
            c.data.name = oname
        EX.select_only([dup] + [c for c, _ in tmp])
        bpy.ops.export_scene.gltf(
            filepath=path, export_format="GLB", use_selection=True, export_yup=True, export_apply=False,
            export_extras=True, export_skins=True, export_influence_nb=4, export_all_influences=False,
            export_def_bones=False, export_leaf_bone=False, export_morph=False, export_animations=False,
            export_normals=True, export_tangents=False, export_attributes=True, export_vertex_color="ACTIVE",
            export_all_vertex_colors=False, export_materials="EXPORT", export_image_format="AUTO",
            export_unused_images=False, export_unused_textures=False)
    finally:
        for c, oname in tmp:
            me = c.data
            bpy.data.objects.remove(c)
            bpy.data.meshes.remove(me)
            o = bpy.data.objects.get(oname + "__b")
            if o is not None:
                o.name = oname
                o.data.name = oname
        arm = dup.data
        bpy.data.objects.remove(dup)
        bpy.data.armatures.remove(arm)
        skel.name = name


def build_style(bb, style_def: dict, assets: str, manifest: dict, report: dict | None = None) -> dict:
    """Build, export and register one style (both variants, four LODs)."""
    import time

    from . import atlas as A
    from . import content
    from . import export as EX
    from . import hair_styles as HS
    from . import pipeline as PL
    from . import rig as RG

    t0 = time.time()
    fr = HF.HeadFrame(bb)
    asset = style_def["asset"]
    short = asset.split("/")[-1]
    occ_extra = [(fr.parts[r][0], fr.parts[r][2]) for r in ("head", "scalp", "neck", "torso") if r in fr.parts]
    out = {}
    coll = PL.collection(f"{bb.id}_hair")
    for cap in (False, True):
        st = HS.style(short, fr, cap)
        lods = []
        for lod in range(4):
            g, paths = build_lod(fr, st, lod, cap)
            lods.append(finish_lod(fr, st, g, paths, cap, occ_extra))
        chains = chain_joints(st, lods[0].paths)
        hides = ["scalp", "ears"] if st.covers_ears else ["scalp"]
        objs = []
        tri = {}
        for lod, hl in enumerate(lods):
            oname = f"hair_{short}{'_cap' if cap else ''}_{bb.id}_lod{lod}"
            o = to_object(oname, hl, bb.material, coll, A)
            o["asset"] = asset
            o["lod"] = lod
            o["hides"] = hides
            o["cap"] = cap
            o["toon"] = "hair"
            o["body"] = bb.id
            # Rest joints of the sway chains (check_hair.py settles them under gravity).
            o["sway_joints"] = {c: [[round(float(x), 5) for x in j] for j in J] for c, J in sorted(chains.items())}
            if lod == 0:
                o["fringe_hem"] = fringe_hem(fr, st, hl) or [[0.0, float(fr.brow_z), 0.0]]
                o["fringe_shadow"] = dict(FRINGE_SHADOW)
                o["sway"] = sorted(chains)
            RG.bind(o, bb.skel)
            objs.append(o)
            tri[f"lod{lod}"] = sum(len(f) - 2 for f in hl.faces)
        rel = content.asset_file(asset, bb.id, cap=cap)
        export_hair_glb(os.path.join(assets, rel), bb.skel, objs, chains)
        entry = manifest["assets"].get(asset)
        if entry is None or entry.get("kind") != "hair":
            entry = {}
            manifest["assets"][asset] = entry
        entry.update({"kind": "hair", "style_id": style_def["id"], "body": bb.id, "hides": hides,
                      "tint": ["hair_colour", None, None], "sway": sorted(chains), "length": st.length,
                      "source": "locks", "budgets": dict(zip(("lod0", "lod1", "lod2", "lod3"), BUDGETS[st.length]))})
        entry.setdefault("cap_files" if cap else "files", {})[bb.id] = rel
        entry.setdefault("cap_triangles" if cap else "triangles", {})[bb.id] = tri
        if not cap:
            entry.setdefault("bounds", {})[bb.id] = EX.bounds([objs[0]])
        for o in objs:
            o.hide_set(True)
            o.hide_render = True
        out["cap" if cap else "hair"] = {"triangles": tri, "file": rel,
                                         "bytes": os.path.getsize(os.path.join(assets, rel)),
                                         "sway_bones": sum(len(chain_bone_names(c)) for c in chains)}
        PL.log(f"exported {rel} {tri}")
    out["seconds"] = round(time.time() - t0, 1)
    if report is not None:
        report[asset] = out
    return out


def remove_retired(assets: str, manifest: dict) -> list[str]:
    """Delete the files and manifest entries of hair styles that are no
    longer shipped (the 14 retired styles of creator.json v1, and any style
    built for a body it no longer belongs to)."""
    from . import content

    shipped = {s["asset"]: s["body"] for s in content.hair_styles()}
    removed = []
    for key in [k for k, v in manifest["assets"].items() if v.get("kind") == "hair" and k not in shipped]:
        del manifest["assets"][key]
    d = os.path.join(assets, "hair")
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if not fn.endswith(".glb"):
                continue
            stem = fn[:-4]
            ok = any(stem in (f"{a.split('/')[-1]}_{b}", f"{a.split('/')[-1]}_cap_{b}") for a, b in shipped.items())
            if not ok:
                os.remove(os.path.join(d, fn))
                removed.append(fn)
    return removed
