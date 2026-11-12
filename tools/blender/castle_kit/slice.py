"""Castle D1: world-aligned edge loops where the baked occlusion changes
(docs/art/castle-kit.md "Shading: the AO bake"; the numbers are grid.py's `SLICE_*`).
Pure Python, no `bpy`. Import it under an alias (`from castle_kit import
slice as SL`): the module name shadows Python's built-in `slice`.

`slice_faces(faces, ctx, uv)` takes a kitted cell's faces (assemble.WFace,
after the pieces and the props kit, before the merge) and returns them with
every `world`-mapped face (walls, floors, ceilings, vault webs, soffits)
cut along world-aligned planes, plus what that added. Mouldings, trims,
props and ribs (`trim`, `fit`, `kit`, `sky`, `grey` faces) already have an
edge at every profile step and are left as they are.

- Faces are grouped by plane (the rounded normal and offset, as the lint's
  z-fighting check) and each plane into touching stretches: every face of a
  stretch is cut by the stretch's planes, so coplanar neighbours meet with
  the same vertices on their shared edges (no T-junction within a plane),
  and a cut never runs on into a separate stretch of the same plane.
- **Walls** (|n.y| below `WALL_NY`): horizontal planes `SLICE_WALL_FROM_FLOOR`
  above and `SLICE_WALL_FROM_TOP` below every walkable surface of the cell
  next to the wall (its floor and its wall top, or the springing in a
  vaulted cell); vertical planes `SLICE_NEAR` either side of every inner
  corner and door jamb (an edge where the wall ends at a floor: not a
  window's jamb, which has wall below it) and of every shaft, pier and
  hero against the wall (`WALL_FEATURES`); the `SLICE_GRID` world grid.
- **Floors and ceilings** (|n.y| above `FLAT_NY`): planes in x and in z at
  `SLICE_NEAR` from every edge where the floor or ceiling ends (the walls
  round a floor, door edges, the gallery edges of a soffit, the corbel line
  along a wall); on floors, `SLICE_FOOTPRINT` round every hero standing on
  it (`FLOOR_HEROES`: tables, benches, the counter, the plinths, the pier
  stack, the stage); the `SLICE_GRID` world grid.
- **Vaults, arch intrados and sloped soffits** (the rest): along the world
  axis the stretch runs furthest on, where it runs more than
  `SLICE_ARC_MAX`, planes on the world grid of `ARC_STEP`
  (`SLICE_ARC_MAX`: no segment stays longer), the same for every web of a
  vault.
- A plane within `SLICE_MERGE` of a plane already kept or of an edge of the
  stretch parallel to it is dropped (no slivers); a horizontal wall plane
  behind a moulding run along the stretch's whole length (the plinth below
  its top, the wainscot) is dropped too (it would cut only hidden wall).
- **The loop budget** (`grid.slice_allowance`, passed in by assemble.py):
  planes are added by tier (0: the contact lines, 1: the second lines and
  the vault rings, 2: the 3.0 m lines, shafts' 2.0 m and the grid; see
  `candidates`), the largest stretch first within a tier, and a plane that
  would take the LOD0 triangles past the budget is left out. So the lint's
  budget holds by construction, and what the budget leaves out is the
  least important plane, never a contact line before a grid line.

Each piece keeps its face's material, mode, source, LOD, ramp and group,
and its UVs are recomputed from its own corners with the world formula
(`uv(material, pts)`), so the mapping does not move.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from castle_kit import grid as G
from castle_kit.pieces import geom

WALL_NY = 0.1  # |normal.y| below this: a wall (vertical cuts along it, horizontal cuts across)
FLAT_NY = 0.9  # above this: a floor (up) or a ceiling (down)
ARC_STEP = G.SLICE_ARC_MAX  # the world grid of the vault and soffit rings: no segment longer than SLICE_ARC_MAX
ARC_AXIS_MAX = 0.7  # a plane cuts a sloped face only where |normal . axis| stays below this (no slivers)
NEAR_WALL = 0.3  # a piece within this of a wall's plane stands against it
ON_FLOOR = (-0.05, 0.3)  # a piece whose lowest point lies this far below / above a floor stands on it
REACH = 1.0  # a surface or a piece this close to a stretch's plan bounds is next to it
PROBE = 0.1  # a wall's ends are found this far above each floor next to it
# Pieces (by role, the placement id's prefix) whose sides cut a wall they stand against,
# and pieces (heroes, the props kit's models) whose footprint rings the floor they stand on.
WALL_FEATURES = ("shaft", "line_shaft", "hero", "fireplace")
FLOOR_HEROES = ("hero", "props")
# Moulding runs against a wall: a horizontal plane inside one along a stretch's whole
# length (HIDE_COVER of it, HIDE_MARGIN inside the run's height) cuts only hidden wall.
RUNS = ("plinth", "wainscot", "cornice", "string", "crest")
HIDE_MARGIN = 0.05
HIDE_COVER = 0.95
EPS = 1e-6


@dataclass(frozen=True)
class Context:
    """What the cuts are taken from: the cell's walkable surfaces as
    (x0, z0, x1, z1, floor y, wall top), and the LOD0 bounds of each placed
    piece and props-kit model as (role, lo, hi)."""

    surfaces: tuple
    pieces: tuple


@dataclass
class Stats:
    faces: int = 0  # world faces looked at
    sliced: int = 0  # of them, cut at least once
    pieces: int = 0  # faces they became
    added: int = 0  # LOD0 triangles the cuts added
    added_all: int = 0  # every triangle the cuts added (LOD1 nodes too)
    planes: int = 0  # cut planes kept over every stretch
    unsliced: int = 0  # the cell's LOD0 triangles before the cuts (assemble.py)
    budget: int | None = None  # the LOD0 triangles the cuts were allowed to add


def context(layout, cell: str, faces, springing: float | None = None) -> Context:
    """The cuts' context of a kitted cell: its flat walkable surfaces (doors
    included: their walls are the passage's) with their floors and wall tops
    (the springing in a vaulted cell), and the bounds of every placed piece
    (faces that are not the Planner's, the floors' or the sky's)."""
    surf = []
    ys = [s.y for s in layout.surfaces if s.cell == cell and s.kind != "door"]
    vault_y = (min(ys) + springing) if (springing is not None and ys) else None
    for s in sorted((s for s in layout.surfaces if s.cell == cell and not s.sloped), key=lambda s: s.id):
        top = G.wall_top(s)
        if vault_y is not None:
            top = min(top, vault_y)
        surf.append((s.x0, s.z0, s.x1, s.z1, s.y, top))
    boxes: dict = {}
    for f in faces:
        if f.lod == "lod1" or f.src in ("base", "floor") or f.src.startswith("sky:"):
            continue
        lo, hi = boxes.get(f.src, ([math.inf] * 3, [-math.inf] * 3))
        for p in f.pts:
            for k in range(3):
                lo[k] = min(lo[k], p[k])
                hi[k] = max(hi[k], p[k])
        boxes[f.src] = (lo, hi)
    pieces = tuple((src.split(":", 1)[0], tuple(lo), tuple(hi)) for src, (lo, hi) in sorted(boxes.items()))
    return Context(tuple(surf), pieces)


# -- geometry ---------------------------------------------------------------------------


def plane_key(pts):
    """The plane a face lies in: its rounded unit normal and offset (2 mm)."""
    n = geom.unit(geom.newell(pts))
    d = geom.dot(n, pts[0])
    return (round(n[0], 3) + 0.0, round(n[1], 3) + 0.0, round(n[2], 3) + 0.0, round(d / 0.002)), n


def split(poly, axis: int, v: float):
    """A convex polygon cut by the plane p[axis] = v: (the part below, the part
    above); either is None when it has no area."""
    below, above = [], []
    m = len(poly)
    for i in range(m):
        a, b = poly[i], poly[(i + 1) % m]
        da, db = a[axis] - v, b[axis] - v
        if da <= 0:
            below.append(a)
        if da >= 0:
            above.append(a)
        if (da < 0 < db) or (db < 0 < da):
            t = da / (da - db)
            p = tuple(v if k == axis else a[k] + (b[k] - a[k]) * t for k in range(3))
            below.append(p)
            above.append(p)
    return _clean(below), _clean(above)


def _clean(poly):
    out = []
    for p in poly:
        if not out or max(abs(p[k] - out[-1][k]) for k in range(3)) > 1e-9:
            out.append(p)
    while len(out) > 1 and max(abs(out[0][k] - out[-1][k]) for k in range(3)) <= 1e-9:
        out.pop()
    if len(out) < 3 or geom.norm(geom.newell(out)) / 2.0 < 1e-7:
        return None
    return out


def _bounds(faces):
    lo = [min(p[k] for f in faces for p in f.pts) for k in range(3)]
    hi = [max(p[k] for f in faces for p in f.pts) for k in range(3)]
    return lo, hi


def _near(lo, hi, box_lo, box_hi, reach) -> bool:
    """Do the plan (x, z) bounds come within `reach` of each other?"""
    return box_lo[0] <= hi[0] + reach and box_hi[0] >= lo[0] - reach and box_lo[2] <= hi[2] + reach and \
        box_hi[2] >= lo[2] - reach


def _offsets(features, dists) -> list[float]:
    return [f + s * d for f in features for d in dists for s in (-1.0, 1.0)]


def _edge_lines(faces, axis: int) -> list[float]:
    """Where the edges a plane p[axis] = v would run along lie (both ends at
    the same coordinate on `axis`)."""
    out = set()
    for f in faces:
        m = len(f.pts)
        for i in range(m):
            a, b = f.pts[i], f.pts[(i + 1) % m]
            if abs(a[axis] - b[axis]) < 1e-4:
                out.add(round((a[axis] + b[axis]) / 2.0, 4))
    return sorted(out)


def _axes_2d(n) -> tuple[int, int]:
    """The two world axes a plane with normal `n` is drawn on (its dominant axis dropped)."""
    ax = max(range(3), key=lambda k: abs(n[k]))
    return tuple(k for k in range(3) if k != ax)


def _covered(polys2d, u: float, v: float) -> bool:
    """Is the 2D point inside one of the convex polygons (either winding)?"""
    for poly in polys2d:
        sgn = 0
        inside = True
        m = len(poly)
        for i in range(m):
            (x0, y0), (x1, y1) = poly[i], poly[(i + 1) % m]
            c = (x1 - x0) * (v - y0) - (y1 - y0) * (u - x0)
            if abs(c) < 1e-12:
                continue
            s = 1 if c > 0 else -1
            if sgn == 0:
                sgn = s
            elif s != sgn:
                inside = False
                break
        if inside:
            return True
    return False


def boundary_lines(faces, n, axis: int, probe=None) -> list[float]:
    """The stretch's edges along which a plane p[axis] = v runs where the
    surface is there on one side only (an inner corner, a door jamb, the wall
    round a floor, a gallery edge), not the edges between two of its own
    faces. `probe(lo, hi)` gives where on the other axis an edge spanning
    lo..hi is tested (default: its middle)."""
    a, b = _axes_2d(n)
    other = b if a == axis else a
    flat = [[(p[axis], p[other]) for p in f.pts] for f in faces]
    out = set()
    for f in faces:
        m = len(f.pts)
        for i in range(m):
            p, q = f.pts[i], f.pts[(i + 1) % m]
            if abs(p[axis] - q[axis]) >= 1e-4:
                continue
            u = (p[axis] + q[axis]) / 2.0
            lo, hi = sorted((p[other], q[other]))
            for v in (probe(lo, hi) if probe else [(lo + hi) / 2.0]):
                if _covered(flat, u - 0.01, v) != _covered(flat, u + 0.01, v):
                    out.add(round(u, 4))
                    break
    return sorted(out)


def components(faces) -> list[list[int]]:
    """The faces of one plane in touching stretches (bounds within 0.1 mm), as
    index lists in a fixed order."""
    boxes = [_bounds([f]) for f in faces]
    parent = list(range(len(faces)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    order = sorted(range(len(faces)), key=lambda i: (boxes[i][0][0], i))
    for k, i in enumerate(order):
        lo_i, hi_i = boxes[i]
        for j in order[k + 1:]:
            lo_j, hi_j = boxes[j]
            if lo_j[0] > hi_i[0] + 1e-4:
                break
            if all(lo_j[d] <= hi_i[d] + 1e-4 and lo_i[d] <= hi_j[d] + 1e-4 for d in range(3)):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
    out: dict = {}
    for i in range(len(faces)):
        out.setdefault(find(i), []).append(i)
    return [out[k] for k in sorted(out)]



# -- the candidate planes of a stretch --------------------------------------------------------
#
# Every candidate is (tier, axis, coordinate). The tiers order what the
# loop budget keeps first (grid.slice_allowance; `slice_faces` adds planes
# tier by tier, the largest stretches first, while the budget lasts):
# 0 the contact lines (a wall's plinth top and 1.5 m, its cornice line, 0.75 m
# from corners, jambs and the walls round a floor or ceiling, a hero's
# +0.3 m ring); 1 the second lines (2.0 m, 1.5 m below the top, shafts and
# heroes against a wall, +1.0 m rings, the vault rings); 2 the 3.0 m lines,
# shafts' 2.0 m and the world grid.


def _hidden(y: float, faces, n, ctx: Context, t: int, c: int) -> bool:
    """Is the height `y` of a wall stretch behind a moulding run along its
    whole length (the plinth, the wainscot, a cornice or string course
    standing against it): a plane there would cut faces nobody sees."""
    lo, hi = _bounds(faces)
    plane_c = sum(p[c] for f in faces for p in f.pts) / sum(len(f.pts) for f in faces)
    spans = []
    for role, blo, bhi in ctx.pieces:
        if role not in RUNS or not (blo[1] + HIDE_MARGIN < y < bhi[1] - HIDE_MARGIN):
            continue
        if not (blo[c] - NEAR_WALL <= plane_c <= bhi[c] + NEAR_WALL):
            continue
        spans.append((max(blo[t], lo[t]), min(bhi[t], hi[t])))
    covered, at = 0.0, lo[t]
    for a, b in sorted(spans):
        if b <= at:
            continue
        covered += b - max(a, at)
        at = b
    return covered >= HIDE_COVER * (hi[t] - lo[t])


def wall_candidates(faces, n, ctx: Context) -> list[tuple]:
    lo, hi = _bounds(faces)
    t = 0 if abs(n[0]) <= abs(n[2]) else 2  # the horizontal axis the wall runs along
    c = 2 if t == 0 else 0  # the axis it faces
    out = []
    # Horizontal planes from the floors and wall tops of the surfaces next to the wall.
    floors = set()
    for x0, z0, x1, z1, y, top in ctx.surfaces:
        if y > hi[1] + EPS or top < lo[1] - EPS:
            continue
        if not _near(lo, hi, (x0, 0.0, z0), (x1, 0.0, z1), REACH):
            continue
        floors.add(y)
        for a, tier in zip(G.SLICE_WALL_FROM_FLOOR, (0, 0, 2)):
            out.append((tier, 1, y + a))
        for a, tier in zip(G.SLICE_WALL_FROM_TOP, (0, 1, 2)):
            out.append((tier, 1, top - a))
    out = [(tier, a, v) for tier, a, v in out if not _hidden(v, faces, n, ctx, t, c)]
    # Vertical planes either side of the inner corners and door jambs (where the
    # wall ends at a floor) and of the shafts, piers and heroes against the wall.
    probe = lambda y0, y1: [f + PROBE for f in sorted(floors) if y0 <= f + PROBE <= y1]  # noqa: E731
    for e in boundary_lines(faces, n, t, probe):
        out += [(tier, t, e + s * d) for d, tier in zip(G.SLICE_NEAR, (0, 1)) for s in (-1.0, 1.0)]
    plane_c = sum(p[c] for f in faces for p in f.pts) / sum(len(f.pts) for f in faces)
    reach = max(G.SLICE_NEAR)
    for role, blo, bhi in ctx.pieces:
        if role not in WALL_FEATURES or blo[1] > hi[1] or bhi[1] < lo[1]:
            continue
        if not (blo[c] - NEAR_WALL <= plane_c <= bhi[c] + NEAR_WALL):
            continue
        if bhi[t] < lo[t] - reach or blo[t] > hi[t] + reach:
            continue
        for d, tier in zip(G.SLICE_NEAR, (1, 2)):
            out += [(tier, t, blo[t] - d), (tier, t, bhi[t] + d)]
    out += [(2, t, v) for v in _grid(lo[t], hi[t], G.SLICE_GRID)]
    return out


def flat_candidates(faces, n, ctx: Context) -> list[tuple]:
    lo, hi = _bounds(faces)
    y = sum(p[1] for f in faces for p in f.pts) / sum(len(f.pts) for f in faces)
    out = []
    for axis in (0, 2):
        for e in boundary_lines(faces, n, axis):
            out += [(tier, axis, e + s * d) for d, tier in zip(G.SLICE_NEAR, (0, 1)) for s in (-1.0, 1.0)]
        if n[1] > 0:
            for role, blo, bhi in ctx.pieces:
                if role in FLOOR_HEROES and _near(lo, hi, blo, bhi, REACH) and ON_FLOOR[0] <= blo[1] - y <= ON_FLOOR[1]:
                    for d, tier in zip(G.SLICE_FOOTPRINT, (0, 1)):
                        out += [(tier, axis, blo[axis] - d), (tier, axis, bhi[axis] + d)]
        out += [(2, axis, v) for v in _grid(lo[axis], hi[axis], G.SLICE_GRID)]
    return out


def arc_candidates(faces, n) -> list[tuple]:
    """Rings along the one world axis the stretch runs furthest on (of those
    its plane is not nearly parallel to), where it runs more than SLICE_ARC_MAX."""
    lo, hi = _bounds(faces)
    axes = [a for a in (0, 1, 2) if abs(n[a]) < ARC_AXIS_MAX and hi[a] - lo[a] > G.SLICE_ARC_MAX]
    if not axes:
        return []
    axis = max(axes, key=lambda a: (hi[a] - lo[a], -a))
    return [(1, axis, v) for v in _grid(lo[axis], hi[axis], ARC_STEP)]


def _grid(lo: float, hi: float, step: float) -> list[float]:
    return [k * step for k in range(math.ceil((lo + EPS) / step), math.floor((hi - EPS) / step) + 1)]


def candidates(faces, n, ctx: Context) -> list[tuple]:
    """The candidate planes of one stretch of coplanar faces, sorted and
    rounded to the micrometre: (tier, axis, coordinate)."""
    if abs(n[1]) < WALL_NY:
        got = wall_candidates(faces, n, ctx)
    elif abs(n[1]) > FLAT_NY:
        got = flat_candidates(faces, n, ctx)
    else:
        got = arc_candidates(faces, n)
    best: dict = {}
    for tier, axis, v in got:
        key = (axis, round(v, 6))
        best[key] = min(tier, best.get(key, tier))
    return sorted((tier, axis, v) for (axis, v), tier in best.items())


# -- the slicing ------------------------------------------------------------------------------


def cut(pts, cuts: dict[int, list[float]]) -> list:
    """A convex face cut by every plane of `cuts` that crosses it, as convex
    pieces in a fixed order (axis by axis, low to high)."""
    pieces = [list(pts)]
    for axis in sorted(cuts):
        for v in sorted(cuts[axis]):
            pieces = _cut_all(pieces, axis, v)
    return pieces


def _cut_all(pieces, axis: int, v: float) -> list:
    out = []
    for poly in pieces:
        lo, hi = min(p[axis] for p in poly), max(p[axis] for p in poly)
        if not (lo + EPS < v < hi - EPS):
            out.append(poly)
            continue
        a, b = split(poly, axis, v)
        out += [q for q in (a, b) if q is not None]
    return out


def _tris(pieces) -> int:
    return sum(len(p) - 2 for p in pieces)


@dataclass
class _Stretch:
    faces: list  # indices into the cell's faces
    lo: list
    hi: list
    edges: dict  # axis -> edge coordinates
    cands: list  # (tier, axis, v)
    area: float
    lod0: list  # per face: counts toward the budget
    pieces: list  # per face: its current convex pieces
    kept: dict  # axis -> kept coordinates


def slice_faces(faces: list, ctx: Context, uv, budget: int | None = None) -> tuple[list, Stats]:
    """The faces with every world-mapped one cut along world-aligned planes
    (castle D1), and what the cuts added. Planes are added tier by tier
    (largest stretch first, then axis and coordinate) while the LOD0
    triangles they add stay within `budget` (None: no limit); a plane within
    SLICE_MERGE of one already kept or of a parallel edge of its stretch is
    never added. `uv(material, pts)` gives a piece's world UVs."""
    st = Stats(budget=budget)
    groups: dict = {}
    normals: dict = {}
    for i, f in enumerate(faces):
        if f.mode != "world":
            continue
        key, n = plane_key(f.pts)
        groups.setdefault(key, []).append(i)
        normals.setdefault(key, n)
    stretches: list[_Stretch] = []
    for key in sorted(groups):
        idx = groups[key]
        for comp in components([faces[i] for i in idx]):
            members = [faces[idx[k]] for k in comp]
            lo, hi = _bounds(members)
            stretches.append(_Stretch(
                [idx[k] for k in comp], lo, hi, {a: _edge_lines(members, a) for a in range(3)},
                candidates(members, normals[key], ctx), sum(f.area() for f in members),
                [f.lod != "lod1" for f in members], [[list(f.pts)] for f in members], {0: [], 1: [], 2: []}))
    order = sorted(((c[0], -round(s.area, 3), k, c[1], c[2]) for k, s in enumerate(stretches) for c in s.cands))
    merge = G.SLICE_MERGE
    spent = 0
    for _, _, k, axis, v in order:
        s = stretches[k]
        if not (s.lo[axis] + merge <= v <= s.hi[axis] - merge):
            continue
        if any(abs(v - e) < merge for e in s.edges[axis]) or any(abs(v - q) < merge for q in s.kept[axis]):
            continue
        new = [_cut_all(p, axis, v) for p in s.pieces]
        cost = sum(_tris(b) - _tris(a) for a, b, l0 in zip(s.pieces, new, s.lod0) if l0)
        if cost == 0 and all(len(a) == len(b) for a, b in zip(s.pieces, new)):
            continue
        if budget is not None and spent + cost > budget:
            continue
        spent += cost
        s.pieces = new
        s.kept[axis].append(v)
        st.planes += 1
    pieces_of: dict[int, list] = {}
    for s in stretches:
        cuts = {a: sorted(v) for a, v in s.kept.items() if v}
        for i in s.faces:
            pieces_of[i] = cut(faces[i].pts, cuts) if cuts else [faces[i].pts]
    out = []
    for i, f in enumerate(faces):
        if i not in pieces_of:
            out.append(f)
            continue
        st.faces += 1
        pieces = pieces_of[i]
        if len(pieces) == 1:
            out.append(f)
            continue
        st.sliced += 1
        st.pieces += len(pieces)
        added = _tris(pieces) - (len(f.pts) - 2)
        st.added_all += added
        if f.lod != "lod1":
            st.added += added
        for p in pieces:
            out.append(replace(f, pts=list(p), uv=uv(f.material, p)))
    return out, st
