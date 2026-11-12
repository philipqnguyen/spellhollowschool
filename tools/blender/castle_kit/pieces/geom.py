"""Geometry kernel of the castle kit pieces (pure Python, no `bpy`).

A piece is built in its own frame and placed by `Placement` (yaw about +Y,
then a translation), like `props.json` kit models:

    x  along the wall, to the right of someone facing the wall
    y  up (0 = the floor or sill the piece stands on)
    z  out of the wall face into the room (the face is z = 0, the wall
       mass behind it is z < 0)

Every face is a convex polygon wound counter-clockwise seen from its front
(concave outlines are ear-clipped into triangles here), with a face tag
`<material>|<uv_mode>` (castle_kit/grid.py) and per-corner UV parameters
whose meaning follows the mode:

    world   None: grid.world_uv after placement
    trim    (s, p) metres: s along the run, p along the profile from its
            bottom; `anchor` faces add the placement's offset along its x
            axis to s, so sweeps and frames continue across pieces
    fit     (a, b) in 0..1 inside the tile
    kit     metres, like meshkit faces (kit.face_uvs maps them into a slot)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]
EPS = 1e-7


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def newell(pts) -> Vec3:
    nx = ny = nz = 0.0
    n = len(pts)
    for i in range(n):
        x, y, z = pts[i]
        x2, y2, z2 = pts[(i + 1) % n]
        nx += (y - y2) * (z + z2)
        ny += (z - z2) * (x + x2)
        nz += (x - x2) * (y + y2)
    return (nx, ny, nz)


def norm(v) -> float:
    return math.sqrt(dot(v, v))


def unit(v) -> Vec3:
    n = norm(v)
    return (v[0] / n, v[1] / n, v[2] / n) if n > EPS else (0.0, 0.0, 0.0)


def rot_y(p, yaw: float) -> Vec3:
    """As meshkit.rot_y: local +Z ends up facing (sin yaw, 0, cos yaw)."""
    s, c = math.sin(yaw), math.cos(yaw)
    return (p[0] * c + p[2] * s, p[1], -p[0] * s + p[2] * c)


@dataclass
class KFace:
    pts: list[Vec3]
    tag: str
    uv: list[tuple[float, float]] | None = None
    anchor: bool = False
    # The named group (grid.GROUP_RE) this face merges into whatever
    # the placement's group (the hourglass set's sand, one group per glass); None: the placement's.
    group: str | None = None

    def normal(self) -> Vec3:
        return unit(newell(self.pts))

    def area(self) -> float:
        return norm(newell(self.pts)) / 2.0


@dataclass
class Piece:
    """A built piece: faces in its own frame; `lod1` replaces them beyond the
    LOD distance (None: the piece has no LOD1); `walkthrough` pieces are
    exempt from the intrusion rule; `meta` carries checkable facts (a
    flight's tread centres, a door's clear box)."""

    name: str
    faces: list[KFace] = field(default_factory=list)
    lod1: list[KFace] | None = None
    walkthrough: bool = False
    meta: dict = field(default_factory=dict)

    def face(self, pts, tag: str, uv=None, normal: Vec3 | None = None, anchor: bool = False) -> None:
        add_face(self.faces, pts, tag, uv, normal, anchor)

    def triangles(self) -> int:
        return sum(len(f.pts) - 2 for f in self.faces)


@dataclass(frozen=True)
class Placement:
    """Where a piece goes: its frame's origin in world metres and its yaw."""

    origin: Vec3
    yaw: float = 0.0

    def point(self, p) -> Vec3:
        return add(rot_y(p, self.yaw), self.origin)

    def right(self) -> Vec3:
        return rot_y((1.0, 0.0, 0.0), self.yaw)

    def s_offset(self) -> float:
        return dot(self.origin, self.right())


def oriented(pts, uv, normal):
    """`pts` (and its UVs) wound so the front faces along `normal`."""
    if normal is not None and dot(newell(pts), normal) < 0:
        return list(reversed(pts)), (list(reversed(uv)) if uv is not None else None)
    return list(pts), (list(uv) if uv is not None else None)


def add_face(out: list, pts, tag: str, uv=None, normal=None, anchor: bool = False) -> None:
    """Appends a face (dropping degenerate ones); a concave polygon is
    ear-clipped into triangles."""
    pts = [tuple(float(c) for c in p) for p in pts]
    if uv is not None:
        uv = [(float(a), float(b)) for a, b in uv]
    pts, uv = dedupe(pts, uv)
    if len(pts) < 3 or norm(newell(pts)) < 1e-9:
        return
    pts, uv = oriented(pts, uv, normal)
    if len(pts) > 3 and not is_convex(pts):
        for tri in ear_clip(pts):
            add_face(out, [pts[i] for i in tri], tag, [uv[i] for i in tri] if uv is not None else None, None, anchor)
        return
    out.append(KFace(pts, tag, uv, anchor))


def dedupe(pts, uv):
    keep_p, keep_u = [], []
    for i, p in enumerate(pts):
        if keep_p and norm(sub(p, keep_p[-1])) < 1e-7:
            continue
        keep_p.append(p)
        if uv is not None:
            keep_u.append(uv[i])
    if len(keep_p) > 1 and norm(sub(keep_p[0], keep_p[-1])) < 1e-7:
        keep_p.pop()
        if uv is not None:
            keep_u.pop()
    return keep_p, (keep_u if uv is not None else None)


def _project(pts):
    """2D coordinates of a planar polygon on its dominant plane, keeping its winding CCW."""
    n = newell(pts)
    ax = max(range(3), key=lambda k: abs(n[k]))
    i, j = [(1, 2), (2, 0), (0, 1)][ax]
    flip = n[ax] < 0
    return [((p[j], p[i]) if flip else (p[i], p[j])) for p in pts]


def _cross2(o, a, b) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def is_convex(pts) -> bool:
    q = _project(pts)
    n = len(q)
    return all(_cross2(q[i], q[(i + 1) % n], q[(i + 2) % n]) >= -1e-10 for i in range(n))


def ear_clip(pts) -> list[tuple[int, int, int]]:
    """Triangles (index triples) of a simple planar polygon wound CCW seen from its front."""
    q = _project(pts)
    idx = list(range(len(q)))
    out = []
    guard = 0
    while len(idx) > 3 and guard < 10000:
        guard += 1
        n = len(idx)
        for k in range(n):
            a, b, c = idx[(k - 1) % n], idx[k], idx[(k + 1) % n]
            if _cross2(q[a], q[b], q[c]) <= 1e-12:
                continue
            if any(_inside(q[m], q[a], q[b], q[c]) for m in idx if m not in (a, b, c)
                   and q[m] != q[a] and q[m] != q[b] and q[m] != q[c]):
                continue
            out.append((a, b, c))
            idx.pop(k)
            break
        else:
            # Numerically flat remainder: fan it.
            break
    if len(idx) >= 3:
        for k in range(1, len(idx) - 1):
            if abs(_cross2(q[idx[0]], q[idx[k]], q[idx[k + 1]])) > 1e-12:
                out.append((idx[0], idx[k], idx[k + 1]))
    return out


def _inside(p, a, b, c) -> bool:
    """Inside or on the boundary of triangle abc (a point on an ear's edge,
    a collinear reflex vertex, must stop that ear)."""
    return _cross2(a, b, p) >= -1e-12 and _cross2(b, c, p) >= -1e-12 and _cross2(c, a, p) >= -1e-12


# -- outlines ----------------------------------------------------------------------


def arch_geometry(span: float, spring: float):
    """(cx, r, theta_apex) of a two-centred arch (grid.ARCH_RISE): the right
    arc's centre is at (-cx, spring), its radius r reaches the right jamb,
    and it meets the centre line at theta_apex."""
    from castle_kit import grid as G

    half = span / 2.0
    rise = G.ARCH_RISE * span
    cx = (rise * rise - half * half) / span
    r = cx + half
    return cx, r, math.atan2(rise, cx)


def pointed_arc(span: float, spring: float, n: int, grow: float = 0.0) -> list[tuple[float, float]]:
    """The intrados (grow = 0) or a curve `grow` metres outside it, from the
    left jamb over the apex to the right jamb: 2n + 1 points (x, y)."""
    cx, r, _ = arch_geometry(span, spring)
    rr = r + grow
    top = math.acos(max(-1.0, min(1.0, cx / rr)))
    # The right arc from its jamb (theta 0) up to the apex.
    up = [(-cx + rr * math.cos(top * i / n), spring + rr * math.sin(top * i / n)) for i in range(n + 1)]
    apex = (0.0, up[-1][1])
    left = [(-x, y) for x, y in up[:-1]]
    return left + [apex] + list(reversed(up[:-1]))


def arc_lengths(pts) -> list[float]:
    out = [0.0]
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        out.append(out[-1] + math.hypot(x1 - x0, y1 - y0))
    return out


def lancet_outline(width: float, height: float, n: int) -> list[tuple[float, float]]:
    """A pointed opening `width` x `height` (apex), bottom at y = 0: CCW from
    the bottom left (convex)."""
    from castle_kit import grid as G

    spring = height - G.ARCH_RISE * width
    arc = pointed_arc(width, spring, n)
    # pointed_arc runs left -> right over the top; CCW wants right -> left.
    return [(-width / 2, 0.0), (width / 2, 0.0)] + list(reversed(arc))


# -- builders --------------------------------------------------------------------


def rect_z(faces, x0, x1, y0, y1, z, tag, facing: float = 1.0, uv=None, anchor=False):
    """A rectangle on the plane z = const facing +z (facing 1) or -z."""
    pts = [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)]
    add_face(faces, pts, tag, uv, (0.0, 0.0, facing), anchor)


def box(faces, lo, hi, tag, skip=(), uv_fn=None):
    """Axis-aligned box faces (local frame); `skip` lists +x -x +y -y +z -z
    sides to leave out; `uv_fn(side, pts)` gives per-corner UVs (or None)."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    sides = {
        "+x": ([(x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1)], (1, 0, 0)),
        "-x": ([(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)], (-1, 0, 0)),
        "+y": ([(x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0)], (0, 1, 0)),
        "-y": ([(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)], (0, -1, 0)),
        "+z": ([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], (0, 0, 1)),
        "-z": ([(x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0)], (0, 0, -1)),
    }
    for side, (pts, n) in sides.items():
        if side in skip:
            continue
        uv = uv_fn(side, pts) if uv_fn else None
        add_face(faces, pts, tag if isinstance(tag, str) else tag(side), uv, n, anchor=uv is not None)


def profile_lengths(profile) -> list[float]:
    out = [0.0]
    for (z0, y0), (z1, y1) in zip(profile, profile[1:]):
        out.append(out[-1] + math.hypot(z1 - z0, y1 - y0))
    return out


def sweep(faces, profile, x0: float, x1: float, tag: str, metres: float, ends=("cap", "cap"), cap_tag: str | None = None,
          y_at=None, rotated: bool = False, closed: bool = False):
    """Extrudes `profile` [(z, y), ...] along x from x0 to x1. The profile
    runs from its bottom edge at the wall (z = 0) out and up and back to the
    wall (counter-clockwise seen from -x): the swept body lies between it
    and the wall. UVs for a trim strip: s = x (anchored), p = the distance
    along the profile scaled to the strip's `metres`.

    `ends` per end: "cap" closes it with the profile's face, "miter" cuts it
    for an inside corner (the sweep of the other wall meets it on the
    diagonal), "outer" for an outside corner, "none" leaves it open.
    `y_at(x)` raises the profile along a slope (balustrades on stairs).
    `closed`: the profile is a closed loop not touching the wall (ribs)."""
    lens = profile_lengths(profile)
    total = lens[-1] or 1.0
    dy = y_at or (lambda x: 0.0)

    def xs(z, end):
        mode = ends[end]
        if end == 0:
            return x0 + z if mode == "miter" else (x0 - z if mode == "outer" else x0)
        return x1 - z if mode == "miter" else (x1 + z if mode == "outer" else x1)

    n = len(profile)
    segs = n if closed else n - 1
    for i in range(segs):
        (za, ya), (zb, yb) = profile[i], profile[(i + 1) % n]
        pa, pb = lens[i] / total * metres, (lens[i + 1] if i + 1 < len(lens) else total) / total * metres
        ax0, bx0, ax1, bx1 = xs(za, 0), xs(zb, 0), xs(za, 1), xs(zb, 1)
        pts = [(ax0, ya + dy(ax0), za), (ax1, ya + dy(ax1), za), (bx1, yb + dy(bx1), zb), (bx0, yb + dy(bx0), zb)]
        if rotated:
            uv = [(ya, ax0), (ya, ax1), (yb, bx1), (yb, bx0)]
        else:
            uv = [(ax0, pa), (ax1, pa), (bx1, pb), (bx0, pb)]
        normal = (0.0, -(zb - za), (yb - ya))
        add_face(faces, pts, tag, uv, normal, anchor=not rotated)
    for end, sgn in ((0, -1.0), (1, 1.0)):
        if ends[end] != "cap":
            continue
        loop = list(profile) if closed else list(profile)
        x = x0 if end == 0 else x1
        pts = [(x, y + dy(x), z) for z, y in loop]
        ymin = min(y for _, y in loop)
        height = (max(y for _, y in loop) - ymin) or 1.0
        uv = [(z, (y - ymin) / height * metres) for z, y in loop]
        add_face(faces, pts, cap_tag or tag, uv, (sgn, 0.0, 0.0), anchor=False)


def polyline_band(faces, inner, outer, z0: float, z1: float, tag: str, metres: float, edges=True,
                  edge_tag: str | None = None, cap_tag: str | None = None, caps=(True, True), front=True):
    """A band between two curves (inner and outer, same point count) on the
    plane z = z1 (its front), standing out from z0, with its inner and
    outer edge faces and end caps: arch rims and frames. UVs on a trim
    strip: s = arc length along the inner curve, p = across 0..metres."""
    s = arc_lengths(inner)
    for i in range(len(inner) - 1):
        a, b = inner[i], inner[i + 1]
        c, d = outer[i + 1], outer[i]
        if front:
            pts = [(a[0], a[1], z1), (b[0], b[1], z1), (c[0], c[1], z1), (d[0], d[1], z1)]
            uv = [(s[i], 0.0), (s[i + 1], 0.0), (s[i + 1], metres), (s[i], metres)]
            add_face(faces, pts, tag, uv, (0.0, 0.0, 1.0))
        if not edges:
            continue
        et = edge_tag or tag
        # Inner edge: faces toward the opening; outer edge: away from it.
        mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
        ox, oy = (c[0] + d[0]) / 2 - mx, (c[1] + d[1]) / 2 - my
        depth = z1 - z0
        pts = [(a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1)]
        add_face(faces, pts, et, [(s[i], 0.0), (s[i + 1], 0.0), (s[i + 1], depth), (s[i], depth)], (-ox, -oy, 0.0))
        pts = [(d[0], d[1], z0), (c[0], c[1], z0), (c[0], c[1], z1), (d[0], d[1], z1)]
        add_face(faces, pts, et, [(s[i], metres - depth), (s[i + 1], metres - depth), (s[i + 1], metres), (s[i], metres)],
                 (ox, oy, 0.0))
    for end, k, k2 in ((0, 0, 1), (1, len(inner) - 1, len(inner) - 2)):
        if not caps[end]:
            continue
        a, d = inner[k], outer[k]
        nxt = inner[k2]
        pts = [(a[0], a[1], z0), (d[0], d[1], z0), (d[0], d[1], z1), (a[0], a[1], z1)]
        # The cap faces away from the band's next segment.
        add_face(faces, pts, cap_tag or tag, [(0.0, 0.0), (0.0, metres), (z1 - z0, metres), (z1 - z0, 0.0)],
                 (a[0] - nxt[0], a[1] - nxt[1], 0.0))


def fan(faces, centre, ring, tag, normal, uv_fn=None):
    """Triangles from `centre` to each consecutive pair of `ring` (3D)."""
    for i in range(len(ring) - 1):
        pts = [centre, ring[i], ring[i + 1]]
        add_face(faces, pts, tag, uv_fn(pts) if uv_fn else None, normal)


def bbox_uv(pts2d):
    """(a, b) in 0..1 of 2D points over their bounding box (fit tiles)."""
    xs = [p[0] for p in pts2d]
    ys = [p[1] for p in pts2d]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = (x1 - x0) or 1.0, (y1 - y0) or 1.0
    return [((x - x0) / w, (y - y0) / h) for x, y in pts2d]
