"""The lake's water and the backdrop round the grounds
(docs/art/castle-kit.md "Exterior"). Pure Python, no `bpy`, deterministic.

Outside the grounds' box the land is one height function, `outside_height`:
the lake (`LAKE`: the box's shoreline continued round the castle crag's west
side and out to the mountains) is water, its bed falling from the waterline;
the land rises from the shore; round the castle the crag is a level shelf a
metre under the ground floor (`CRAG_Y`) whose edges drop as cliffs to the
water; near the box it meets the box's edge a little below the terrain (whose
skirts hide the seam). Under the cellars (`exterior.json` `no_outside`) the
shelf is left out, so it never shows inside them.

* `water_mesh(g, ...)`: `lake.lake_water`, flat at the water's height: 4 m
  squares over the box's lake and shore, 16 m squares round it, wide tiles to
  `WATER_FAR` beyond; nothing on the castle crag (`CRAG_HOLE`). TEXCOORD_0
  world-mapped (Blender (x / 16, -z / 16)), COLOR_0 the depth factor (0 at the
  shoreline, 1 from `DEPTH_FULL` m down; r = g = b).
* `rock_mesh(...)`: `backdrop.backdrop_rock`: the castle crag and the land west
  of the box (16 m squares), and the mountains ringing the lake (two ranges,
  flat-shaded, 600-1,600 m out, the north lower so the castle's skyline reads
  against the sky).
* `forest_mesh(...)`: `backdrop.backdrop_forest`: the deep forest east of
  the box and round the lake's east shore: the forest's dark inner mass, a
  smooth canopy over the land (16 m squares, `CANOPY` m over the ground,
  gently bumpy) and a curtain down to the ground wherever it ends.
  Vegetation look A: the trees themselves are `forest_plantings(...)`, the
  foliage kit's pines and broadleaves standing on the land on a jittered
  grid (`FOREST_SPACING`), their crowns over the mass; the manifest lists
  them in cell `backdrop` and the client draws them as impostors (LOD3,
  three crossed quads each).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import foliage as FO
import grounds_terrain as GT

CRAG_Y = -1.0
CRAG_HOLE = (-144.0, -128.0, 112.0, 92.0)  # x0, z0, x1, z1: no water drawn here (the castle crag)
STEP = 16.0
NEAR = (-240.0, -260.0, 304.0, 460.0)  # the 16 m grid's extent (multiples of 16 from the box's corners)
WATER_FAR = 1700.0
DEPTH_FULL = 4.0
CANOPY = (5.0, 1.5)  # the inner mass's top over the ground (m), and its jitter (the trees stand over it)
FOREST_SPACING = 8.0  # metres between the backdrop's trees (a jittered grid, a divisor of the 16 m squares)
FOREST_SCALE = (1.4, 1.9)  # their scale: 12-17 m pines, crowns overlapping over the mass
# Every backdrop tree's crown and the forest's mass keep this far from the castle's plan
# rectangles: an outer wall (2 m) and a 1 m margin (2026-11-03: the old forest's
# crowns stood 0.9 m into the Clock Tower Courtyard).
CASTLE_CLEAR = 3.0
CASTLE_ZONE = (-122.0, -90.0, 108.0, 92.0)  # the crag shelf's extent before its cliffs
CLIFF = 22.0  # metres over which the shelf drops to the lake bed
LAKE_BED = -22.0
EDGE_BLEND = 20.0
EDGE_DROP = 0.3
EDGE_MAX_DROP = 1.0  # the lint: the backdrop just beyond the terrain's drawn end stands at most this far under it

# The lake's outline outside the box, continuing the box's shoreline (grounds.json) at both ends:
# west round the crag's foot, out to the far side beyond the mountains, and back along the east shore.
LAKE = [(140.0, 228.0), (178.0, 300.0), (222.0, 400.0), (300.0, 520.0), (520.0, 620.0), (900.0, 620.0),
        (1300.0, 900.0), (1200.0, 1700.0), (-1200.0, 1700.0), (-1600.0, 400.0), (-1300.0, -500.0),
        (-420.0, -300.0), (-230.0, -210.0), (-172.0, -130.0), (-150.0, -40.0), (-147.0, 40.0), (-152.0, 120.0),
        (-160.0, 176.0), (-118.0, 186.0), (-100.0, 200.0), (-84.0, 216.0), (-56.0, 236.0), (-16.0, 250.0),
        (30.0, 256.0), (76.0, 252.0), (106.0, 242.0)]
FOREST_X = 128.0  # the canopy starts at the box's east edge (the foliage's first rows stand in front of it)
FOREST_NORTH_X = 104.0  # ... and north of the box, east of the crag shelf


def _hash(i: int, j: int, seed: int) -> float:
    s = (i * 73856093) ^ (j * 19349663) ^ (seed * 83492791)
    s &= 0xFFFFFFFF
    s ^= s >> 13
    s = (s * 1274126177) & 0xFFFFFFFF
    return (s >> 8) / float(1 << 24)


def noise(x: float, z: float, cell: float, seed: int) -> float:
    """Smooth value noise in 0..1."""
    fx, fz = x / cell, z / cell
    i, j = math.floor(fx), math.floor(fz)
    tx, tz = fx - i, fz - j
    tx, tz = tx * tx * (3 - 2 * tx), tz * tz * (3 - 2 * tz)
    a, b = _hash(i, j, seed), _hash(i + 1, j, seed)
    c, d = _hash(i, j + 1, seed), _hash(i + 1, j + 1, seed)
    return a + (b - a) * tx + (c - a) * tz + (a - b - c + d) * tx * tz


def _inside(poly, x: float, z: float) -> bool:
    inside = False
    n = len(poly)
    for k in range(n):
        (xa, za), (xb, zb) = poly[k], poly[(k + 1) % n]
        if (za > z) != (zb > z) and x < xa + (z - za) * (xb - xa) / (zb - za):
            inside = not inside
    return inside


def shore_distance(x: float, z: float) -> float:
    """Signed distance to the lake's outline: negative in the water."""
    n = len(LAKE)
    d = min(GT.seg_dist(x, z, LAKE[k], LAKE[(k + 1) % n]) for k in range(n))
    return -d if _inside(LAKE, x, z) else d


@dataclass
class Backdrop:
    """What the backdrop reads: the grounds, the cellars' plan rectangles."""

    g: GT.Grounds
    cellars: list = field(default_factory=list)  # (x0, z0, x1, z1), grown
    castle: list = field(default_factory=list)  # the castle's plan rectangles (x0, z0, x1, z1): the forest keeps clear

    def castle_clear(self, x: float, z: float, r: float) -> bool:
        """Whether a circle of radius r at (x, z) stays CASTLE_CLEAR metres from every castle
        rectangle (its outer walls stand on the rectangles' edges)."""
        for x0, z0, x1, z1 in self.castle:
            dx = max(x0 - x, 0.0, x - x1)
            dz = max(z0 - z, 0.0, z - z1)
            if math.hypot(dx, dz) < r + CASTLE_CLEAR:
                return False
        return True

    def box(self):
        return (self.g.x0, self.g.z0, self.g.x0 + (self.g.nx - 1) * self.g.step, self.g.z0 + (self.g.nz - 1) * self.g.step)

    def in_box(self, x: float, z: float, eps: float = 1e-6) -> bool:
        x0, z0, x1, z1 = self.box()
        return x0 - eps <= x <= x1 + eps and z0 - eps <= z <= z1 + eps

    def edge_height(self, x: float, z: float) -> tuple[float, float]:
        """(the lowest box-edge ground within a step of the nearest box point, distance to the box)."""
        x0, z0, x1, z1 = self.box()
        cx, cz = min(max(x, x0), x1), min(max(z, z0), z1)
        d = math.hypot(x - cx, z - cz)
        s = STEP / 2.0
        if cx in (x0, x1):
            hs = [self.g.height(cx, cz + t) for t in (-s, -s / 2, 0.0, s / 2, s)]
        else:
            hs = [self.g.height(cx + t, cz) for t in (-s, -s / 2, 0.0, s / 2, s)]
        if cx in (x0, x1) and cz in (z0, z1):
            hs += [self.g.height(cx + t, cz) for t in (-s, s)]
        return min(hs), d

    def outside_height(self, x: float, z: float) -> float:
        d = shore_distance(x, z)
        if d < 0:
            h = max(-15.0 + 0.3 * d, LAKE_BED)
        else:
            h = -15.0 + min(d * 0.25, 14.0) + 4.0 * (noise(x, z, 60.0, 3) - 0.5) * min(d / 30.0, 1.0)
        # The crag shelf round the castle and its cliffs.
        x0, z0, x1, z1 = CASTLE_ZONE
        dz = math.hypot(max(x0 - x, 0.0, x - x1), max(z0 - z, 0.0, z - z1))
        w = 1.0 - GT.smoothstep(0.0, CLIFF, dz)
        h = h + (CRAG_Y - h) * w
        # Meet the box's edge a little below the terrain.
        eh, db = self.edge_height(x, z)
        t = GT.smoothstep(0.0, EDGE_BLEND, db)
        return (eh - EDGE_DROP) + (h - (eh - EDGE_DROP)) * t

    def ground(self, x: float, z: float) -> float:
        return self.g.height(x, z) if self.in_box(x, z) else self.outside_height(x, z)

    def under_cellar(self, x0: float, z0: float, x1: float, z1: float) -> bool:
        return any(x0 < c1 and x1 > c0 and z0 < d1 and z1 > d0 for c0, d0, c1, d1 in self.cellars)


@dataclass
class Mesh:
    """A plain mesh in world metres (y up): verts, polygons (CCW from their front),
    per-vertex grey (COLOR_0), optional per-vertex uv (Blender), flat polygons."""

    name: str
    material: str
    verts: list = field(default_factory=list)
    polys: list = field(default_factory=list)
    grey: list = field(default_factory=list)
    uv: list | None = None
    flat: set = field(default_factory=set)  # indices of polygons shaded flat
    _index: dict = field(default_factory=dict, repr=False)

    def vert(self, x: float, y: float, z: float, grey: float = 1.0, uv=None, key=None) -> int:
        k = key if key is not None else (round(x, 3), round(y, 3), round(z, 3))
        got = self._index.get(k)
        if got is not None:
            return got
        self._index[k] = len(self.verts)
        self.verts.append((round(x, 4), round(y, 4), round(z, 4)))
        self.grey.append(round(min(max(grey, 0.0), 1.0), 4))
        if uv is not None:
            if self.uv is None:
                self.uv = []
            self.uv.append(uv)
        return len(self.verts) - 1

    def poly(self, idx, up: bool = True, flat: bool = False) -> None:
        """A polygon wound to face up (`up`) or as given (`up` None)."""
        idx = list(idx)
        if up is not None:
            pts = [self.verts[k] for k in idx]
            ny = sum((pts[k][2] - pts[(k + 1) % len(pts)][2]) * (pts[k][0] + pts[(k + 1) % len(pts)][0])
                     for k in range(len(pts)))
            if (ny > 0) != up:
                idx.reverse()
        if flat:
            self.flat.add(len(self.polys))
        self.polys.append(tuple(idx))

    def triangles(self) -> int:
        return sum(len(p) - 2 for p in self.polys)


# -- the water ---------------------------------------------------------------------------------


def _depth_grey(bd: Backdrop, x: float, z: float) -> float:
    depth = bd.g.water_y - bd.ground(x, z)
    return min(max(depth / DEPTH_FULL, 0.0), 1.0)


def water_mesh(bd: Backdrop, name: str = "lake.lake_water") -> Mesh:
    g = bd.g
    wy = g.water_y
    m = Mesh(name, "lake_water")

    def v(x, z, grey=None):
        gr = _depth_grey(bd, x, z) if grey is None else grey
        return m.vert(x, wy, z, gr, (x / 16.0, -z / 16.0), key=(round(x, 3), round(z, 3)))

    def square(x0, z0, x1, z1, grey=None):
        m.poly([v(x0, z0, grey), v(x1, z0, grey), v(x1, z1, grey), v(x0, z1, grey)])

    bx0, bz0, bx1, bz1 = bd.box()
    # The box: 4 m squares where the lake bed or the shore's foot shows (any corner under the water + 0.2).
    s = 2 * g.step
    nx, nz = int(round((bx1 - bx0) / s)), int(round((bz1 - bz0) / s))
    for j in range(nz):
        for i in range(nx):
            x0, z0 = bx0 + i * s, bz0 + j * s
            if min(g.height(x0 + a, z0 + b) for a in (0.0, s / 2, s) for b in (0.0, s / 2, s)) < wy + 0.2:
                square(x0, z0, x0 + s, z0 + s)
    # Round the box: 16 m squares within NEAR where the ground dips under the water.
    nx0, nz0, nx1, nz1 = NEAR
    hx0, hz0, hx1, hz1 = CRAG_HOLE
    for j in range(int(round((nz1 - nz0) / STEP))):
        for i in range(int(round((nx1 - nx0) / STEP))):
            x0, z0 = nx0 + i * STEP, nz0 + j * STEP
            x1, z1 = x0 + STEP, z0 + STEP
            if x0 >= bx0 - 1e-6 and x1 <= bx1 + 1e-6 and z0 >= bz0 - 1e-6 and z1 <= bz1 + 1e-6:
                continue
            if x0 >= hx0 - 1e-6 and x1 <= hx1 + 1e-6 and z0 >= hz0 - 1e-6 and z1 <= hz1 + 1e-6:
                continue
            if min(bd.ground(x0 + a, z0 + b) for a in (0.0, STEP / 2, STEP) for b in (0.0, STEP / 2, STEP)) < wy + 0.2:
                square(x0, z0, x1, z1)
    # Beyond NEAR: wide deep tiles out to WATER_FAR (west, east, north, south strips).
    F = WATER_FAR
    T = 400.0
    for x0, z0, x1, z1 in ((-F, -F, nx0, F), (nx1, -F, F, F), (nx0, -F, nx1, nz0), (nx0, nz1, nx1, F)):
        nxt, nzt = max(1, int(math.ceil((x1 - x0) / T))), max(1, int(math.ceil((z1 - z0) / T)))
        for b in range(nzt):
            for a in range(nxt):
                xa, xb = x0 + (x1 - x0) * a / nxt, x0 + (x1 - x0) * (a + 1) / nxt
                za, zb = z0 + (z1 - z0) * b / nzt, z0 + (z1 - z0) * (b + 1) / nzt
                square(xa, za, xb, zb, grey=1.0)
    return m


# -- the rock: the crag, the land west of the box, the mountains -------------------------------


def _grid_squares(bd: Backdrop, want) -> list:
    """16 m squares of NEAR (outside the box) for which `want(x0, z0, x1, z1)` holds."""
    nx0, nz0, nx1, nz1 = NEAR
    bx0, bz0, bx1, bz1 = bd.box()
    out = []
    for j in range(int(round((nz1 - nz0) / STEP))):
        for i in range(int(round((nx1 - nx0) / STEP))):
            x0, z0 = nx0 + i * STEP, nz0 + j * STEP
            x1, z1 = x0 + STEP, z0 + STEP
            if x0 >= bx0 - 1e-6 and x1 <= bx1 + 1e-6 and z0 >= bz0 - 1e-6 and z1 <= bz1 + 1e-6:
                continue
            if want(x0, z0, x1, z1):
                out.append((x0, z0, x1, z1))
    return out


def _forest_square(x0: float, z0: float, x1: float, z1: float) -> bool:
    cx = (x0 + x1) / 2
    cz = (z0 + z1) / 2
    return cx > FOREST_X or (cx > FOREST_NORTH_X and cz < 92.0)


def rock_mesh(bd: Backdrop, name: str = "backdrop.backdrop_rock") -> Mesh:
    m = Mesh(name, "backdrop_rock")
    wy = bd.g.water_y

    def want(x0, z0, x1, z1):
        if _forest_square(x0, z0, x1, z1) or bd.under_cellar(x0, z0, x1, z1):
            return False
        hs = [bd.outside_height(x, z) for x in (x0, x1) for z in (z0, z1)]
        return max(hs) > wy - 1.0  # some of it shows above the water

    def v(x, z):
        h = bd.outside_height(x, z) if not bd.in_box(x, z) else bd.g.height(x, z) - EDGE_DROP
        shade = 0.62 + 0.38 * GT.smoothstep(wy - 2.0, wy + 14.0, h)
        return m.vert(x, h, z, shade)

    for x0, z0, x1, z1 in _grid_squares(bd, want):
        ring = _edge_ring(bd, x0, z0, x1, z1)
        if len(ring) == 4:
            m.poly([v(x, z) for x, z in ring])
            continue
        # A square along the box's edge: its side there follows the terrain's edge at every
        # lattice point, EDGE_DROP under it, as a fan from the square's centre.
        c = v((x0 + x1) / 2.0, (z0 + z1) / 2.0)
        idx = [v(x, z) for x, z in ring]
        for k in range(len(idx)):
            m.poly([c, idx[k], idx[(k + 1) % len(idx)]])
    _mountains(m, bd)
    return m


def _edge_ring(bd: Backdrop, x0: float, z0: float, x1: float, z1: float) -> list:
    """The square's corners in order, with the lattice points between them on any side
    lying along the box's edge (within the box's span) put in.

    Grounds edges (docs/architecture.md): the box-edge vertices used to sit at the
    lowest box-edge ground within half a square (`edge_height`) and run straight
    between corners 16 m apart, so beside a slope along the edge (the crag strip
    rising 0.6 m a metre) the backdrop fell up to 5 m under the terrain's edge, past
    its skirt: a sheer cut with a slot under it. Now the side follows the terrain's
    edge exactly (it is straight between lattice points), EDGE_DROP under it."""
    bx0, bz0, bx1, bz1 = bd.box()
    step = bd.g.step
    corners = [(x0, z0), (x1, z0), (x1, z1), (x0, z1)]
    out = []
    for k in range(4):
        (ax, az), (bx, bz) = corners[k], corners[(k + 1) % 4]
        out.append((ax, az))
        if ax == bx and (abs(ax - bx0) < 1e-6 or abs(ax - bx1) < 1e-6):
            lo, hi = max(min(az, bz), bz0), min(max(az, bz), bz1)
            ts = [lo + step * i for i in range(int(math.floor((hi - lo) / step + 1e-6)) + 1)]
            ts = [t for t in ts if min(az, bz) + 1e-6 < t < max(az, bz) - 1e-6]
            out += [(ax, t) for t in (ts if bz > az else reversed(ts))]
        elif az == bz and (abs(az - bz0) < 1e-6 or abs(az - bz1) < 1e-6):
            lo, hi = max(min(ax, bx), bx0), min(max(ax, bx), bx1)
            ts = [lo + step * i for i in range(int(math.floor((hi - lo) / step + 1e-6)) + 1)]
            ts = [t for t in ts if min(ax, bx) + 1e-6 < t < max(ax, bx) - 1e-6]
            out += [(t, az) for t in (ts if bx > ax else reversed(ts))]
    return out


def surface_height(m: Mesh, x: float, z: float):
    """The highest point of the mesh's polygons (fanned) straight over or under (x, z), or None."""
    best = None
    for poly in m.polys:
        pts = [m.verts[k] for k in poly]
        if not (min(p[0] for p in pts) - 1e-6 <= x <= max(p[0] for p in pts) + 1e-6
                and min(p[2] for p in pts) - 1e-6 <= z <= max(p[2] for p in pts) + 1e-6):
            continue
        a = pts[0]
        for b, c in zip(pts[1:], pts[2:]):
            det = (b[2] - c[2]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[2] - c[2])
            if abs(det) < 1e-12:
                continue
            l1 = ((b[2] - c[2]) * (x - c[0]) + (c[0] - b[0]) * (z - c[2])) / det
            l2 = ((c[2] - a[2]) * (x - c[0]) + (a[0] - c[0]) * (z - c[2])) / det
            l3 = 1.0 - l1 - l2
            if min(l1, l2, l3) >= -1e-6:
                y = l1 * a[1] + l2 * b[1] + l3 * c[1]
                best = y if best is None else max(best, y)
    return best


def edge_gaps(bd: Backdrop, rock: Mesh, max_drop: float = EDGE_MAX_DROP, step: float = 1.0) -> list[str]:
    """Grounds edges (docs/architecture.md): every metre round the terrain's drawn end
    (the box's west, east and south edges; on the castle side the castle lip's far edge,
    `GT.CASTLE_LIP` beyond the box), the backdrop's rock a quarter metre further out
    stands at most `max_drop` under the terrain's edge. A taller drop shows from the
    grounds as a sheer cut with the lake through the slot under it (the terrain's skirts
    face out, so from inside they are not drawn). Where no rock is drawn there (the
    forest, which hangs its own curtain, the cellars under the castle, the lake) or a
    castle floor crosses the lip (`lip_gaps`), nothing is asked."""
    g = bd.g
    bx0, bz0, bx1, bz1 = bd.box()
    lip = g.lip_gaps
    out = []

    def check(side, x, z, h, ox, oz):
        r = surface_height(rock, x + ox, z + oz)
        if r is not None and h - r > max_drop + 1e-6:
            out.append(f"backdrop: {side} edge at [{x:.1f}, {z:.1f}]: the terrain ends at {h:.2f}, "
                       f"the rock beyond at {r:.2f} ({h - r:.2f} m drop, at most {max_drop})")

    n = int(round((bx1 - bx0) / step))
    for i in range(n + 1):
        x = bx0 + i * step
        if not any(x0 - 1e-6 <= x <= x1 + 1e-6 for x0, x1 in lip):
            check("north", x, bz0 - GT.CASTLE_LIP, g.height(x, bz0), 0.0, -0.25)
        check("south", x, bz1, g.height(x, bz1), 0.0, 0.25)
    n = int(round((bz1 - bz0) / step))
    for j in range(n + 1):
        z = bz0 + j * step
        check("west", bx0, z, g.height(bx0, z), -0.25, 0.0)
        check("east", bx1, z, g.height(bx1, z), 0.25, 0.0)
    return out


# Mountain ranges: (centre x, z, base radius, crest heights (low, high), north scale, segments, seed).
RANGES = ((0.0, 300.0, 1050.0, (70.0, 170.0), 0.5, 64, 41), (0.0, 300.0, 1500.0, (140.0, 260.0), 0.6, 48, 42))


def _mountains(m: Mesh, bd: Backdrop) -> None:
    for cx, cz, r0, (lo, hi), north, seg, seed in RANGES:
        ring_in, crest, ring_out = [], [], []
        for k in range(seg):
            a = 2 * math.pi * k / seg
            sx, sz = math.cos(a), math.sin(a)
            # The north (toward the castle's back, -z) is lower so the skyline stays the castle's.
            nf = north + (1.0 - north) * GT.smoothstep(-0.9, 0.2, sz)
            r = r0 * (0.9 + 0.25 * noise(k * 1.7, 0.0, 3.0, seed))
            peak = (lo + (hi - lo) * noise(k * 2.3, 5.0, 2.2, seed + 1)) * nf
            if k % 3 == 1:
                peak *= 0.72  # saddles between the peaks
            xi, zi = cx + (r - 260.0) * sx, cz + (r - 260.0) * sz
            ring_in.append(m.vert(xi, min(bd.outside_height(xi, zi), bd.g.water_y) - 6.0, zi, 0.62))
            xc, zc = cx + r * sx, cz + r * sz
            crest.append(m.vert(xc, peak, zc, 1.0))
            xo, zo = cx + (r + 320.0) * sx, cz + (r + 320.0) * sz
            ring_out.append(m.vert(xo, peak * 0.45, zo, 0.8))
        for k in range(seg):
            k1 = (k + 1) % seg
            m.poly([ring_in[k], ring_in[k1], crest[k1]], flat=True)
            m.poly([ring_in[k], crest[k1], crest[k]], flat=True)
            m.poly([crest[k], crest[k1], ring_out[k1]], flat=True)
            m.poly([crest[k], ring_out[k1], ring_out[k]], flat=True)


# -- the forest --------------------------------------------------------------------------------


def _canopy(bd: Backdrop, x: float, z: float) -> float:
    """The inner mass's top over the ground, jittered per vertex (smooth-shaded), lower at its
    front edges."""
    ground = bd.outside_height(x, z) if not bd.in_box(x, z) else bd.g.height(x, z)
    x_edge = FOREST_X if z >= 92.0 else FOREST_NORTH_X
    ramp = 0.7 + 0.3 * GT.smoothstep(x_edge, x_edge + 2 * STEP, x)
    jitter = (_hash(int(round(x)), int(round(z)), 9) - 0.5) * 2.0
    return max(ground, bd.g.water_y) + (CANOPY[0] + CANOPY[1] * 0.5 * jitter) * ramp


def forest_mesh(bd: Backdrop, name: str = "backdrop.backdrop_forest") -> Mesh:
    m = Mesh(name, "backdrop_forest")
    wy = bd.g.water_y

    def land(x, z):
        return bd.outside_height(x, z) if not bd.in_box(x, z) else bd.g.height(x, z)

    def want(x0, z0, x1, z1):
        if not _forest_square(x0, z0, x1, z1):
            return False
        if not bd.castle_clear((x0 + x1) / 2, (z0 + z1) / 2, STEP * 0.7072):
            return False  # nothing of the mass near the castle's walls
        return min(land(x, z) for x in (x0, x1) for z in (z0, z1)) > wy + 0.5

    def top(x, z):
        return m.vert(x, _canopy(bd, x, z), z, 0.7 + 0.3 * _hash(int(round(x)), int(round(z)), 12))

    squares = set(_grid_squares(bd, want))
    for x0, z0, x1, z1 in sorted(squares):
        a, b, c, d = top(x0, z0), top(x1, z0), top(x1, z1), top(x0, z1)
        m.poly([a, b, c])
        m.poly([a, c, d])
    # Wherever the canopy ends: a curtain from its edge down below the ground, facing out (the
    # trees standing along the edge hide it).
    for x0, z0, x1, z1 in sorted(squares):
        for (ax, az), (bx, bz), (ox, oz) in (((x0, z0), (x1, z0), (0, -STEP)), ((x1, z0), (x1, z1), (STEP, 0)),
                                             ((x1, z1), (x0, z1), (0, STEP)), ((x0, z1), (x0, z0), (-STEP, 0))):
            if (x0 + ox, z0 + oz, x1 + ox, z1 + oz) in squares:
                continue
            ta, tb = top(ax, az), top(bx, bz)
            ba = m.vert(ax, min(land(ax, az), wy) - 2.0, az, 0.5, key=("c", ax, az))
            bb = m.vert(bx, min(land(bx, bz), wy) - 2.0, bz, 0.5, key=("c", bx, bz))
            _facing_out(m, [ta, tb, bb, ba], (ox, oz))
    return m


def forest_plantings(bd: Backdrop) -> list:
    """The backdrop forest's trees (vegetation look A): on a jittered FOREST_SPACING grid over
    the forest's squares, standing on the land (never in the water), mostly pines, scaled
    FOREST_SCALE, turned at random (deterministic)."""
    wy = bd.g.water_y

    def land(x, z):
        return bd.outside_height(x, z) if not bd.in_box(x, z) else bd.g.height(x, z)

    def want(x0, z0, x1, z1):
        if not _forest_square(x0, z0, x1, z1):
            return False
        return min(land(x, z) for x in (x0, x1) for z in (z0, z1)) > wy + 0.5

    out = []
    n = int(round(STEP / FOREST_SPACING))
    cell = STEP / n
    for x0, z0, x1, z1 in sorted(_grid_squares(bd, want)):
        for j in range(n):
            for i in range(n):
                ix, iz = int(round(x0 / cell)) + i, int(round(z0 / cell)) + j
                x = x0 + (i + 0.2 + 0.6 * _hash(ix, iz, 21)) * cell
                z = z0 + (j + 0.2 + 0.6 * _hash(ix, iz, 22)) * cell
                y = land(x, z)
                if y < wy + 0.3:
                    continue
                r = _hash(ix, iz, 23)
                kind = "broad" if r < 0.22 else "pine"
                scale = FOREST_SCALE[0] + (FOREST_SCALE[1] - FOREST_SCALE[0]) * _hash(ix, iz, 24)
                p = FO.tree(kind, 9.0 if kind == "pine" else 7.5, int(r * 1000), pos=(round(x, 3), round(y, 3), round(z, 3)),
                            yaw=round(2 * math.pi * _hash(ix, iz, 25), 4), scale=round(scale, 4))
                if not bd.castle_clear(x, z, FO.species_radius(p.species) * p.scale()):
                    continue  # its crown would reach the castle's walls
                out.append(p)
    return out


def _facing_out(m: Mesh, idx, out) -> None:
    pts = [m.verts[k] for k in idx]
    ex, ez = pts[1][0] - pts[0][0], pts[1][2] - pts[0][2]
    # (p1 - p0) x (p3 - p0) for a quad hanging down from p0-p1: its normal is (ez, 0, -ex) times the depth.
    if ez * out[0] - ex * out[1] > 0:
        m.poly(idx, up=None)
    else:
        m.poly(list(reversed(idx)), up=None)


