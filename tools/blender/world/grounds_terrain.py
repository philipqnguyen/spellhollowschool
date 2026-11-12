"""The grounds' terrain meshes from the committed height files
(docs/art/castle-kit.md "Exterior"). Pure Python, no `bpy`, deterministic.

* `Grounds.load(terrain_dir)`: `grounds.json` and the 30 `<cell>.json` height
  files stitched into one lattice (2 m; neighbours share their border samples),
  read with sim-core's bilinear rule (`height`).
* `terrain_mesh(g, cell, ...)`: the cell's terrain at LOD0. Over the walkable
  area it is the lattice itself, refined where it must be, so the drawn ground
  is the walked ground to well under `grid.P3A_HEIGHT_TOLERANCE` (2 cm):
  - a plain square is two triangles split along the same diagonal everywhere
    ((i, j) to (i + 1, j + 1));
  - a square whose bilinear surface is twisted enough that the diagonal would
    leave its centre more than `TWIST_FAN` off gets a centre vertex (four
    triangles: at most a sixteenth of the twist off anywhere);
  - a square a gravel path crosses is cut into four 1 m squares (eight
    triangles), so the path's mask edge is crisp; the new vertices sit on the
    bilinear surface, and the lattice edges stay straight lines (the bilinear
    surface is linear along them), so nothing cracks against a plain neighbour.
  The box's outer edges (the castle side, the crag, the forest, the lake bed)
  carry a skirt `SKIRT` m deep facing outward, so the coarser backdrop never
  shows a crack. On the castle side (the box's north edge) the
  ground first runs on `CASTLE_LIP` m under the castle's outer walls at the
  edge's height (render only: nothing walkable changes), so a wall whose face
  stands short of the box's edge (the courtyard's south wall at z 91.2)
  meets the grass; the skirt hangs from the lip's far edge. Not where a
  castle floor crosses that strip (the courtyard gate, the covered bridge).
* `terrain_lod1(g, cell)`: beyond `grid.P3A_GROUNDS_LOD` (60 m): 4 m squares
  inside the cell and the full 2 m lattice along its border (fans from each
  border square's centre sample), so LOD0 and LOD1 cells meet edge to edge.
* Vertex data (both LODs): TEXCOORD_0 world-mapped at the turf's tile (Blender
  (x / 4.8, -z / 4.8), as `castle_floor_grass`); TEXCOORD_1 the blend mask
  (glTF u = gravel path weight, v = rock weight; turf is the rest): paths from
  `grounds.json` `paths` soft over about 1 m, the beach (turf toward the path
  colour within about a metre of the waterline), rock where the slope passes
  about 0.45 and on the crag strip; COLOR_0 the baked shade (greyscale: the
  hollows, the foot of the castle side, of buildings, walls, stones and
  rocks); normals from the whole lattice, so cells and LODs light alike.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

TILE_M = 4.8  # castle_textures.json castle_floor_grass tile_m (the turf)
TWIST_FAN = 0.012  # a square's diagonal leaves its bilinear centre by twist / 4; over this it gets a centre vertex
PATH_SOFT = 0.7  # the path's mask fades over this many metres at its edge
SKIRT = 3.0  # the box's outer edges hang a skirt this deep
CASTLE_LIP = 1.5  # The ground runs on this far north of the box, under the castle's outer walls
SHADE_MIN = 0.5
BEACH = (-14.45, -13.85)  # ground heights over which the beach (path colour) gives way to turf
ROCK_SLOPE = (0.42, 0.56)  # slopes over which turf gives way to rock
CRAG_ROCK = (-103.0, -107.0)  # x over which the crag strip turns to rock


def smoothstep(e0: float, e1: float, v: float) -> float:
    t = min(max((v - e0) / (e1 - e0), 0.0), 1.0)
    return t * t * (3.0 - 2.0 * t)


def seg_dist(px: float, pz: float, a, b) -> float:
    ax, az = a
    bx, bz = b
    dx, dz = bx - ax, bz - az
    ll = dx * dx + dz * dz
    t = 0.0 if ll < 1e-12 else min(max(((px - ax) * dx + (pz - az) * dz) / ll, 0.0), 1.0)
    return math.hypot(px - (ax + t * dx), pz - (az + t * dz))


def polyline_dist(px: float, pz: float, pts) -> float:
    return min(seg_dist(px, pz, a, b) for a, b in zip(pts, pts[1:]))


@dataclass
class Grounds:
    """The grounds' facts (`grounds.json`) and the stitched lattice."""

    meta: dict
    x0: float
    z0: float
    step: float
    nx: int
    nz: int
    h: list  # rows of z, each a row of x
    cell_size: float
    walk: list = field(default_factory=list)  # walkable rectangles (x0, z0, x1, z1, Heights) from the layout
    lip_gaps: list = field(default_factory=list)  # (x0, x1) of castle floors crossing the castle lip's strip

    @classmethod
    def load(cls, terrain_dir: str, layout=None) -> "Grounds":
        with open(os.path.join(terrain_dir, "grounds.json"), encoding="utf-8") as f:
            meta = json.load(f)
        (bx0, bz0), (bx1, bz1) = meta["box"]["min"], meta["box"]["max"]
        step = float(meta["step"])
        nx, nz = int(round((bx1 - bx0) / step)) + 1, int(round((bz1 - bz0) / step)) + 1
        h = [[None] * nx for _ in range(nz)]
        for cell in meta["cells"]:
            with open(os.path.join(terrain_dir, f"{cell}.json"), encoding="utf-8") as f:
                c = json.load(f)
            i0, j0 = int(round((c["origin"][0] - bx0) / step)), int(round((c["origin"][1] - bz0) / step))
            for b in range(c["rows"]):
                for a in range(c["cols"]):
                    v = float(c["heights"][b * c["cols"] + a])
                    old = h[j0 + b][i0 + a]
                    if old is not None and abs(old - v) > 1e-9:
                        raise ValueError(f"{cell}: border sample [{a}, {b}] disagrees with its neighbour")
                    h[j0 + b][i0 + a] = v
        if any(v is None for row in h for v in row):
            raise ValueError("the cells' height files do not cover the box")
        g = cls(meta, float(bx0), float(bz0), step, nx, nz, h, float(meta["cell_size"]))
        if layout is not None:
            g.walk = [(s.x0, s.z0, s.x1, s.z1, s.heights) for s in layout.surfaces
                      if s.kind == "grounds" and s.heights is not None and s.cell in meta["cells"]]
            g.lip_gaps = sorted((s.x0, s.x1) for s in layout.surfaces
                                if s.cell not in meta["cells"] and s.z1 > bz0 - CASTLE_LIP + 1e-6 and s.z0 < bz0 - 1e-6)
        return g

    # -- the lattice -------------------------------------------------------------------

    @property
    def water_y(self) -> float:
        return float(self.meta["water_y"])

    def sample(self, i: int, j: int) -> float:
        return self.h[min(max(j, 0), self.nz - 1)][min(max(i, 0), self.nx - 1)]

    def height(self, x: float, z: float) -> float:
        """sim-core's `Heights::height_at` on the whole lattice (clamped)."""
        fx = min(max((x - self.x0) / self.step, 0.0), self.nx - 1.0)
        fz = min(max((z - self.z0) / self.step, 0.0), self.nz - 1.0)
        i, j = min(int(fx), self.nx - 2), min(int(fz), self.nz - 2)
        tx, tz = fx - i, fz - j
        h = self.h
        a = h[j][i] + (h[j][i + 1] - h[j][i]) * tx
        b = h[j + 1][i] + (h[j + 1][i + 1] - h[j + 1][i]) * tx
        return a + (b - a) * tz

    def slope(self, x: float, z: float) -> float:
        """The ground's gradient (rise per metre) by central differences over a lattice step."""
        d = self.step
        gx = (self.height(x + d, z) - self.height(x - d, z)) / (2 * d)
        gz = (self.height(x, z + d) - self.height(x, z - d)) / (2 * d)
        return math.hypot(gx, gz)

    def normal(self, x: float, z: float):
        """Unit normal (world, y up) by central differences, the same in every cell and LOD."""
        d = self.step / 2.0
        gx = (self.height(x + d, z) - self.height(x - d, z)) / (2 * d)
        gz = (self.height(x, z + d) - self.height(x, z - d)) / (2 * d)
        n = (-gx, 1.0, -gz)
        ln = math.sqrt(n[0] ** 2 + 1.0 + n[2] ** 2)
        return (n[0] / ln, n[1] / ln, n[2] / ln)

    def cell_origin(self, cell: str) -> tuple[int, int]:
        """(i0, j0) of a cell's first lattice square."""
        r, c = cell_rc(cell)
        n = int(round(self.cell_size / self.step))
        return c * n, r * n

    def walkable(self, x: float, z: float, eps: float = 1e-6) -> bool:
        return any(x0 - eps <= x <= x1 + eps and z0 - eps <= z <= z1 + eps for x0, z0, x1, z1, _ in self.walk)

    def walk_height(self, x: float, z: float, eps: float = 1e-6):
        """The height of the walkable rectangle under (x, z) (its own `heights`), or None."""
        for x0, z0, x1, z1, hf in self.walk:
            if x0 - eps <= x <= x1 + eps and z0 - eps <= z <= z1 + eps:
                return hf.height(x, z)
        return None

    def square_walkable(self, i: int, j: int) -> bool:
        x, z = self.x0 + (i + 0.5) * self.step, self.z0 + (j + 0.5) * self.step
        return self.walkable(x, z, eps=-1e-3)

    def drawn_height(self, x: float, z: float, eps: float = 1e-6):
        """The drawn ground's height at (x, z): the lattice inside the box, the castle lip's
        (the box edge's height carried `CASTLE_LIP` north, but where a castle floor crosses
        it); None elsewhere (the backdrop's)."""
        (bx0, bz0), (bx1, bz1) = self.meta["box"]["min"], self.meta["box"]["max"]
        if not bx0 - eps <= x <= bx1 + eps:
            return None
        if bz0 - eps <= z <= bz1 + eps:
            return self.height(x, z)
        if bz0 - CASTLE_LIP - eps <= z < bz0 and not any(x0 < x < x1 for x0, x1 in self.lip_gaps):
            return self.height(x, bz0)
        return None

    def twist(self, i: int, j: int) -> float:
        h = self.h
        return abs(h[j][i] + h[j + 1][i + 1] - h[j][i + 1] - h[j + 1][i])


def cell_rc(cell: str) -> tuple[int, int]:
    _, r, c = cell.split("_")
    return int(r[1:]), int(c[1:])


# -- the mask and the shade -----------------------------------------------------------------


@dataclass(frozen=True)
class Occluder:
    """Something standing on the ground that darkens its foot: a rectangle
    (x0, z0, x1, z1) grown by `reach` metres, `depth` the darkest shade lost."""

    x0: float
    z0: float
    x1: float
    z1: float
    reach: float = 1.5
    depth: float = 0.28

    def factor(self, x: float, z: float) -> float:
        dx = max(self.x0 - x, 0.0, x - self.x1)
        dz = max(self.z0 - z, 0.0, z - self.z1)
        d = math.hypot(dx, dz)
        return 1.0 - self.depth * (1.0 - smoothstep(0.0, self.reach, d))


class Painter:
    """Per-vertex mask (path, rock) and shade of the terrain."""

    def __init__(self, g: Grounds, occluders=()):
        self.g = g
        self.paths = [(float(p["width"]) / 2.0, [tuple(q) for q in p["points"]]) for p in g.meta["paths"].values()]
        self.occ = list(occluders)
        self.edge_z = float(g.meta["box"]["min"][1])

    def path_weight(self, x: float, z: float) -> float:
        w = 0.0
        for half, pts in self.paths:
            d = polyline_dist(x, z, pts)
            w = max(w, 1.0 - smoothstep(half - PATH_SOFT / 2.0, half + PATH_SOFT / 2.0, d))
        return w

    def near_path(self, x: float, z: float, reach: float) -> bool:
        return any(polyline_dist(x, z, pts) < half + PATH_SOFT / 2.0 + reach for half, pts in self.paths)

    def mask(self, x: float, z: float) -> tuple[float, float]:
        """(path, rock) weights in 0..1 at (x, z)."""
        g = self.g
        h = g.height(x, z)
        beach = 1.0 - smoothstep(BEACH[0], BEACH[1], h)
        path = max(self.path_weight(x, z), beach)
        rock = smoothstep(ROCK_SLOPE[0], ROCK_SLOPE[1], g.slope(x, z))
        rock = max(rock, smoothstep(CRAG_ROCK[0], CRAG_ROCK[1], x))
        rock *= 1.0 - path * 0.85  # a path keeps its gravel over a steep bit
        return round(path, 4), round(rock, 4)

    def shade(self, x: float, z: float) -> float:
        g = self.g
        h = g.height(x, z)
        d = g.step
        ring = (g.height(x + d, z) + g.height(x - d, z) + g.height(x, z + d) + g.height(x, z - d)) / 4.0
        hollow = min(max((ring - h) * 0.6, 0.0), 0.16)  # a hollow (the ring above the point) darkens
        s = 1.0 - hollow
        s *= 0.78 + 0.22 * smoothstep(self.edge_z, self.edge_z + 4.0, z)  # the castle side's foot
        for o in self.occ:
            if o.x0 - o.reach <= x <= o.x1 + o.reach and o.z0 - o.reach <= z <= o.z1 + o.reach:
                s *= o.factor(x, z)
        return round(min(max(s, SHADE_MIN), 1.0), 4)


# -- meshes ---------------------------------------------------------------------------------


@dataclass
class TerrainMesh:
    """A terrain mesh in world metres (y up): verts, triangles (CCW seen from
    above), per-vertex normal, uv0 (Blender), uv1 (Blender: (path, 1 - rock)),
    shade; `skirt` marks the skirt's vertices (below the ground by design)."""

    name: str
    verts: list = field(default_factory=list)
    tris: list = field(default_factory=list)
    normals: list = field(default_factory=list)
    uv0: list = field(default_factory=list)
    uv1: list = field(default_factory=list)
    shade: list = field(default_factory=list)
    skirt: set = field(default_factory=set)
    _index: dict = field(default_factory=dict, repr=False)

    def triangles(self) -> int:
        return len(self.tris)

    def vert(self, g: Grounds, painter: Painter, x: float, z: float, y: float | None = None, skirt: bool = False) -> int:
        key = (round(x, 4), round(z, 4), skirt)
        got = self._index.get(key)
        if got is not None:
            return got
        if y is None:
            y = g.height(x, z)
        idx = len(self.verts)
        self._index[key] = idx
        self.verts.append((x, round(y, 4), z))
        n = g.normal(x, z)
        self.normals.append(n)
        self.uv0.append((x / TILE_M, -z / TILE_M))
        p, r = painter.mask(x, z)
        self.uv1.append((p, 1.0 - r))
        self.shade.append(painter.shade(x, z) * (0.8 if skirt else 1.0))
        if skirt:
            self.skirt.add(idx)
        return idx

    def tri(self, a: int, b: int, c: int) -> None:
        """A triangle wound CCW seen from above (+y): in the x-z plane with z
        south, that is clockwise in (x, z)."""
        (ax, _, az), (bx, _, bz), (cx, _, cz) = self.verts[a], self.verts[b], self.verts[c]
        cross = (bx - ax) * (cz - az) - (bz - az) * (cx - ax)
        self.tris.append((a, b, c) if cross < 0 else (a, c, b))

    def bounds(self):
        lo = tuple(min(v[k] for v in self.verts) for k in range(3))
        hi = tuple(max(v[k] for v in self.verts) for k in range(3))
        return lo, hi


def _quad(m: TerrainMesh, g: Grounds, pt: Painter, xa: float, za: float, s: float) -> None:
    """Two triangles over the square from (xa, za), `s` on a side, split (xa, za)-(xa+s, za+s)."""
    a = m.vert(g, pt, xa, za)
    b = m.vert(g, pt, xa + s, za)
    c = m.vert(g, pt, xa + s, za + s)
    d = m.vert(g, pt, xa, za + s)
    m.tri(a, b, c)
    m.tri(a, c, d)


def _fan(m: TerrainMesh, g: Grounds, pt: Painter, ring, centre) -> None:
    """Triangles from `centre` (x, z) round the closed `ring` of (x, z) points."""
    c = m.vert(g, pt, *centre)
    idx = [m.vert(g, pt, x, z) for x, z in ring]
    for k in range(len(idx)):
        m.tri(c, idx[k], idx[(k + 1) % len(idx)])


def _skirts(m: TerrainMesh, g: Grounds, pt: Painter, cell: str, edge_pts) -> None:
    """Skirts on the cell's edges that lie on the box's outer edge, each facing out
    of the box, from the ground's edge vertices (`edge_pts`: side -> sorted points)."""
    (bx0, bz0), (bx1, bz1) = g.meta["box"]["min"], g.meta["box"]["max"]
    for side, pts in edge_pts.items():
        on_box = {"n": pts[0][1] <= bz0 + 1e-6, "s": pts[0][1] >= bz1 - 1e-6,
                  "w": pts[0][0] <= bx0 + 1e-6, "e": pts[0][0] >= bx1 - 1e-6}[side]
        if not on_box:
            continue
        for (xa, za), (xb, zb) in zip(pts, pts[1:]):
            ta, tb = m.vert(g, pt, xa, za), m.vert(g, pt, xb, zb)
            if side == "n" and not any(x0 < xb - 1e-6 and xa < x1 - 1e-6 for x0, x1 in g.lip_gaps):
                # The castle lip (module doc): the edge's heights carried CASTLE_LIP north.
                za, zb = za - CASTLE_LIP, zb - CASTLE_LIP
                la = m.vert(g, pt, xa, za, g.height(xa, za))
                lb = m.vert(g, pt, xb, zb, g.height(xb, zb))
                m.tri(ta, tb, lb)
                m.tri(ta, lb, la)
                ta, tb = la, lb
            ba = m.vert(g, pt, xa, za, g.height(xa, za) - SKIRT, skirt=True)
            bb = m.vert(g, pt, xb, zb, g.height(xb, zb) - SKIRT, skirt=True)
            out = {"n": (0, -1), "s": (0, 1), "w": (-1, 0), "e": (1, 0)}[side]
            # (ta, tb, bb) faces along (tb - ta) x (bb - ta), which is (ez, 0, -ex) times the depth.
            ex, ez = xb - xa, zb - za
            if ez * out[0] - ex * out[1] > 0:
                m.tris.append((ta, tb, bb))
                m.tris.append((ta, bb, ba))
            else:
                m.tris.append((ta, bb, tb))
                m.tris.append((ta, ba, bb))


def terrain_mesh(g: Grounds, cell: str, painter: Painter, name: str | None = None) -> TerrainMesh:
    """The cell's LOD0 terrain (module doc)."""
    m = TerrainMesh(name or f"{cell}.grounds_terrain")
    i0, j0 = g.cell_origin(cell)
    n = int(round(g.cell_size / g.step))
    s = g.step
    half = s / 2.0
    for j in range(j0, j0 + n):
        for i in range(i0, i0 + n):
            xa, za = g.x0 + i * s, g.z0 + j * s
            cx, cz = xa + half, za + half
            if painter.near_path(cx, cz, half * 1.5):
                for b in range(2):
                    for a in range(2):
                        _quad(m, g, painter, xa + a * half, za + b * half, half)
            elif g.square_walkable(i, j) and g.twist(i, j) / 4.0 > TWIST_FAN:
                ring = [(xa, za), (xa + s, za), (xa + s, za + s), (xa, za + s)]
                _fan(m, g, painter, ring, (cx, cz))
            else:
                _quad(m, g, painter, xa, za, s)
    _skirts(m, g, painter, cell, _edge_points(m, g, cell))
    return m


def _edge_points(m: TerrainMesh, g: Grounds, cell: str) -> dict:
    """The mesh's vertices on each of the cell's four edges, sorted along it."""
    i0, j0 = g.cell_origin(cell)
    xa, za = g.x0 + i0 * g.step, g.z0 + j0 * g.step
    xb, zb = xa + g.cell_size, za + g.cell_size
    pts = [(v[0], v[2]) for v in m.verts]
    out = {
        "n": sorted({p for p in pts if abs(p[1] - za) < 1e-6}),
        "s": sorted({p for p in pts if abs(p[1] - zb) < 1e-6}),
        "w": sorted({p for p in pts if abs(p[0] - xa) < 1e-6}, key=lambda p: p[1]),
        "e": sorted({p for p in pts if abs(p[0] - xb) < 1e-6}, key=lambda p: p[1]),
    }
    return out


def terrain_lod1(g: Grounds, cell: str, painter: Painter) -> TerrainMesh:
    """The cell's LOD1 terrain (module doc): 4 m squares, the border at 2 m."""
    m = TerrainMesh(f"{cell}.grounds_terrain_lod1")
    i0, j0 = g.cell_origin(cell)
    n = int(round(g.cell_size / g.step))
    s = g.step
    big = 2  # lattice squares per LOD1 square side
    for b in range(0, n, big):
        for a in range(0, n, big):
            xa, za = g.x0 + (i0 + a) * s, g.z0 + (j0 + b) * s
            L = big * s
            border_n, border_s = b == 0, b + big == n
            border_w, border_e = a == 0, a + big == n
            if not (border_n or border_s or border_w or border_e):
                _quad(m, g, painter, xa, za, L)
                continue
            # A fan from the centre sample round the square, with the 2 m samples on border sides.
            ring = [(xa, za)]
            if border_n:
                ring.append((xa + s, za))
            ring.append((xa + L, za))
            if border_e:
                ring.append((xa + L, za + s))
            ring.append((xa + L, za + L))
            if border_s:
                ring.append((xa + s, za + L))
            ring.append((xa, za + L))
            if border_w:
                ring.append((xa, za + s))
            _fan(m, g, painter, ring, (xa + s, za + s))
    _skirts(m, g, painter, cell, _edge_points(m, g, cell))
    return m


# -- checks (the lint, tests) -------------------------------------------------------------


class _TriIndex:
    """Triangles of a mesh's top faces in 2 m buckets, for height-at-point queries."""

    def __init__(self, m: TerrainMesh):
        self.m = m
        self.b: dict = {}
        for t in m.tris:
            if any(k in m.skirt for k in t):
                continue
            xs = [m.verts[k][0] for k in t]
            zs = [m.verts[k][2] for k in t]
            for bi in range(int(math.floor(min(xs) / 2.0)), int(math.floor(max(xs) / 2.0)) + 1):
                for bj in range(int(math.floor(min(zs) / 2.0)), int(math.floor(max(zs) / 2.0)) + 1):
                    self.b.setdefault((bi, bj), []).append(t)

    def height(self, x: float, z: float):
        for t in self.b.get((int(math.floor(x / 2.0)), int(math.floor(z / 2.0))), []):
            (ax, ay, az), (bx, by, bz), (cx, cy, cz) = (self.m.verts[k] for k in t)
            det = (bz - cz) * (ax - cx) + (cx - bx) * (az - cz)
            if abs(det) < 1e-12:
                continue
            l1 = ((bz - cz) * (x - cx) + (cx - bx) * (z - cz)) / det
            l2 = ((cz - az) * (x - cx) + (ax - cx) * (z - cz)) / det
            l3 = 1.0 - l1 - l2
            if min(l1, l2, l3) >= -1e-6:
                return l1 * ay + l2 * by + l3 * cy
        return None


def height_errors(g: Grounds, m: TerrainMesh, cell: str, tol: float) -> list[str]:
    """Every vertex of the mesh's top over a walkable rectangle, and every 1 m point of
    the cell's walkable lattice squares (corners, edge midpoints, centres), within `tol`
    of the walkable rectangle's own heightfield."""
    errs = []
    for k, (x, y, z) in enumerate(m.verts):
        if k in m.skirt:
            continue
        w = g.walk_height(x, z)
        if w is not None and abs(w - y) > tol:
            errs.append(f"{m.name}: vertex [{x}, {z}] at {y:.3f}, the ground {w:.3f}")
    idx = _TriIndex(m)
    i0, j0 = g.cell_origin(cell)
    n = int(round(g.cell_size / g.step))
    for j in range(j0, j0 + n):
        for i in range(i0, i0 + n):
            if not g.square_walkable(i, j):
                continue
            for a in (0.0, 0.5, 1.0):
                for b in (0.0, 0.5, 1.0):
                    x, z = g.x0 + (i + a) * g.step, g.z0 + (j + b) * g.step
                    y = idx.height(x, z)
                    w = g.walk_height(x, z)
                    if y is None:
                        errs.append(f"{m.name}: no ground drawn at [{x}, {z}]")
                    elif w is not None and abs(w - y) > tol:
                        errs.append(f"{m.name}: [{x}, {z}] drawn at {y:.3f}, the ground {w:.3f}")
    return errs


FOOT_TOL = 0.02  # a wall's foot may stand this far over the drawn ground (rounding)
FOOT_REACH = 2.0  # ... and a face whose lowest edge stands higher is a storey, a lintel or a sill, not a foot


def foot_errors(g: Grounds, name: str, polys, tol: float = FOOT_TOL, every: float = 0.5,
                reach: float = FOOT_REACH) -> list[str]:
    """Grounds edges (docs/architecture.md): every vertical face among `polys` (lists of
    (x, y, z) in world metres, wound to face out) whose lowest edge stands over the drawn
    ground (`Grounds.drawn_height`, a few centimetres out from the face) reaches down to
    it: sampled every `every` m along that edge. A foot over the ground shows the sky,
    or whatever lies behind the wall, through the slot under it (the courtyard's south
    wall hung up to 0.58 m over the grass, its paving showing under it)."""
    out = []
    for poly in polys:
        n = len(poly)
        # Newell's normal: a vertical face has none in y.
        nx = sum((poly[k][1] - poly[(k + 1) % n][1]) * (poly[k][2] + poly[(k + 1) % n][2]) for k in range(n))
        ny = sum((poly[k][2] - poly[(k + 1) % n][2]) * (poly[k][0] + poly[(k + 1) % n][0]) for k in range(n))
        nz = sum((poly[k][0] - poly[(k + 1) % n][0]) * (poly[k][1] + poly[(k + 1) % n][1]) for k in range(n))
        ln = math.sqrt(nx * nx + ny * ny + nz * nz)
        if ln < 1e-9 or abs(ny) / ln > 0.05:
            continue
        ox, oz = nx / ln * 0.05, nz / ln * 0.05
        y0 = min(p[1] for p in poly)
        bottom = [p for p in poly if abs(p[1] - y0) < 1e-4]
        if len(bottom) < 2:
            continue
        a = min(bottom, key=lambda p: (p[0], p[2]))
        b = max(bottom, key=lambda p: (p[0], p[2]))
        length = math.hypot(b[0] - a[0], b[2] - a[2])
        steps = max(1, int(math.ceil(length / every)))
        worst = None
        inset = min(0.05, length / 2.0) / max(length, 1e-9)  # not the corners: a lintel's ends sit on its jambs
        for k in range(steps + 1):
            t = inset + (1.0 - 2.0 * inset) * k / steps
            x, z = a[0] + (b[0] - a[0]) * t + ox, a[2] + (b[2] - a[2]) * t + oz
            h = g.drawn_height(x, z)
            if h is not None and h + tol < y0 <= h + reach and (worst is None or y0 - h > worst[0]):
                worst = (y0 - h, x, z, h)
        if worst is not None:
            d, x, z, h = worst
            out.append(f"{name}: a wall's foot at {y0:.2f} stands {d:.2f} m over the ground ({h:.2f}) at [{x:.1f}, {z:.1f}]")
    return out
