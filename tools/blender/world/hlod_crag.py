"""The castle crag's rock faces under the outer walls
(docs/architecture.md). Pure Python (no `bpy`), deterministic.

The castle stands on the crag shelf (world/grounds_backdrop.py, `CRAG_Y`, a
metre under the ground floor) whose edges fall to the lake. From the lake
the shelf read as a smooth grey slope and the castle as standing on a level
hill. This draws rock where the outer walls do not meet walkable ground:
`exterior.json` `crag.runs`, each a line along the walls' foot (`inner`,
outside every playable space and off the grounds' box) with the rock thrown
out to one side (`side`: the outward normal turns left or right of the
line's direction) as a rough strip of flat-shaded facets:

    inner (a little over the ground, piled against the walls)
      -> shoulder (`rim` x 0.45 out, near the shelf's height)
      -> rim (`rim` m out: the cliff's edge)
      -> ledge (part way down the cliff)
      -> foot (`foot` m out, under the water or the ground there)

The rim and the foot stand where the backdrop's ground has already fallen
away, so the cliff face hides the smooth slope behind it; every point of
the strip is kept at or over the backdrop's ground plus `CLEAR`, so no
facet sinks into the slope it covers. A run fades into the ground over its
first and last `TAPER` m (heights sink below the ground, offsets shrink),
so its ends need no caps. Heights come from the same ground the backdrop
uses (`grounds_backdrop.Backdrop`), the walkable grounds are never touched.

The mesh is one chunk's (`crag.chunk`), on `crag.material` (the backdrop's
toon row; its colour the palette's `crag_rock`, world/hlod_export.py).
COLOR_0 greyscale: tops lit, the faces a step darker, the lower cliff in
shade.
"""

from __future__ import annotations

import math
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

STATION_M = 4.0  # stations along a run, before jitter
TAPER = 12.0  # metres at each end over which a run sinks into the ground
CLEAR = 0.15  # every strip point stays at least this far over the backdrop's ground
PILE = (1.2, 3.6)  # the rock's height over the ground at the walls' foot (min, max)
SHOULDER = (-0.4, 1.6)  # the shoulder over the shelf / ground
RIM = (-0.8, 1.2)  # the rim over the shelf / ground
LEDGE = 0.55  # the ledge's share of the drop from the rim to the foot
FOOT_DROP = 1.5  # the foot under the water or the ground
JIT_OUT = 1.6  # the rim's and the ledge's in-and-out jitter (m)
SHADE_TOP, SHADE_FACE, SHADE_LOW = 0.95, 0.78, 0.62


def _hash(i: int, j: int, seed: int) -> float:
    s = (i * 73856093) ^ (j * 19349663) ^ (seed * 83492791)
    s &= 0xFFFFFFFF
    s ^= s >> 13
    s = (s * 1274126177) & 0xFFFFFFFF
    return (s >> 8) / float(1 << 24)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def stations(points, step: float = STATION_M) -> list[tuple[float, float, float, float, float]]:
    """(x, z, nx, nz, s) along the polyline every `step` m (its corners kept),
    with the left normal (nx, nz) of the segment and the distance s from the
    start."""
    out = []
    s = 0.0
    for k in range(len(points) - 1):
        (ax, az), (bx, bz) = points[k], points[k + 1]
        length = math.hypot(bx - ax, bz - az)
        if length < 1e-9:
            continue
        ux, uz = (bx - ax) / length, (bz - az) / length
        n = max(1, int(round(length / step)))
        for i in range(n + (1 if k == len(points) - 2 else 0)):
            t = i / n
            out.append((ax + (bx - ax) * t, az + (bz - az) * t, -uz, ux, s + length * t))
        s += length
    return out


def profile(run: dict, ground, shelf: float) -> list[list[tuple[float, float, float]]]:
    """Each station's five points (inner, shoulder, rim, ledge, foot) in world
    (x, y, z)."""
    sts = stations(run["inner"])
    total = sts[-1][4]
    sign = 1.0 if run["side"] == "left" else -1.0
    seed = sum(ord(c) for c in run["id"])
    rim_d, foot_d = run["rim"], run["foot"]
    rows = []
    for k, (x, z, nx, nz, s) in enumerate(sts):
        # Corners: average the normals of the two segments meeting there.
        if 0 < k < len(sts) - 1 and abs(sts[k - 1][2] - nx) + abs(sts[k - 1][3] - nz) > 1e-6:
            px, pz = sts[k - 1][2] + nx, sts[k - 1][3] + nz
            ln = math.hypot(px, pz) or 1.0
            nx, nz = px / ln, pz / ln
        ox, oz = nx * sign, nz * sign
        fade = min(1.0, s / TAPER, (total - s) / TAPER) if total > 2 * TAPER else 1.0
        h = [_hash(k, j, seed) for j in range(6)]

        def at(d, jit=0.0):
            dd = d * (0.35 + 0.65 * fade) + jit * fade
            return x + ox * dd, z + oz * dd

        def over(px, pz, lift):
            g = ground(px, pz)
            return max(g + CLEAR, lift)

        sink = (1.0 - fade) * 2.5
        xi, zi = at(0.0)
        yi = ground(xi, zi) + _lerp(*PILE, h[0]) * fade - sink
        xs, zs = at(rim_d * 0.45, (h[1] - 0.5) * JIT_OUT)
        ys = over(xs, zs, max(shelf, ground(xs, zs)) + _lerp(*SHOULDER, h[2]) * fade - sink)
        xr, zr = at(rim_d, (h[3] - 0.5) * 2 * JIT_OUT)
        yr = over(xr, zr, max(shelf, ground(xr, zr)) + _lerp(*RIM, h[4]) * fade - sink)
        xf, zf = at(foot_d, (h[5] - 0.5) * JIT_OUT)
        yf = min(ground(xf, zf), run.get("water", ground(xf, zf))) - FOOT_DROP
        xl, zl = at(_lerp(rim_d, foot_d, 0.5), (h[2] - 0.5) * 2 * JIT_OUT)
        yl = over(xl, zl, _lerp(yr, yf, LEDGE + (h[0] - 0.5) * 0.3))
        yi = max(yi, ground(xi, zi) - 0.6)
        rows.append([(xi, yi, zi), (xs, ys, zs), (xr, yr, zr), (xl, yl, zl), (xf, yf, zf)])
    return rows


def triangles(runs, ground, shelf: float) -> list[tuple[list, float, tuple]]:
    """Every run's strip as triangles: (points, grey, an outward hint)."""
    out = []
    shades = (SHADE_TOP, SHADE_TOP, SHADE_FACE, SHADE_LOW)
    for run in runs:
        rows = profile(run, ground, shelf)
        for a, b in zip(rows, rows[1:]):
            for j in range(4):
                p00, p01, p10, p11 = a[j], a[j + 1], b[j], b[j + 1]
                # Outward-and-up hint: the strip faces away from the walls and up.
                hint = (p01[0] - p00[0], 1.0 if j < 2 else 0.3, p01[2] - p00[2])
                out.append(([p00, p10, p11], shades[j], hint))
                out.append(([p00, p11, p01], shades[j], hint))
    return out


_GROUND: dict = {}


def backdrop_ground(terrain_dir: str):
    """The backdrop's ground (the grounds' heights inside their box, the
    backdrop's own outside it) and the crag shelf's height."""
    got = _GROUND.get(terrain_dir)
    if got is None:
        import grounds_backdrop as BD
        import grounds_terrain as GT

        g = GT.Grounds.load(terrain_dir)
        bd = BD.Backdrop(g)
        got = (bd.ground, BD.CRAG_Y)
        _GROUND[terrain_dir] = got
    return got
