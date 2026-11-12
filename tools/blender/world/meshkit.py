"""A tiny pure-Python mesh builder for the scripted world art (no `bpy`).

Dorm shells and furniture are modelled here as plain polygons so the unit
tests can check them (bounds, footprints, triangle counts, dye zones) with any
Python; `bpyutil.py` turns a `Mesh` into Blender objects for export.

Coordinates follow docs/architecture.md: metres, right-handed, **Y up**,
x = east, z = south, yaw 0 faces +Z. Faces are wound counter-clockwise seen
from their front. Every face carries:

* `material`: a role name (`"wood"`, `"velvet"`, `"glass"`...) that the
  builder maps to a real material (shells) or an atlas tile (furniture);
* `zone`: the dye zone of furniture (0 = undyed, 1 = primary, 2 = secondary,
  3 = trim, exported as the `COLOR_0` mask);
* per-corner UVs in metres (texture density is applied by the builder);
* `smooth`: smooth faces share vertices within their group so normals blend.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]
EPS = 1e-6


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a):
    return math.sqrt(dot(a, a))


def normalize(a):
    n = length(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > EPS else (0.0, 0.0, 0.0)


def lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def newell(points) -> Vec3:
    nx = ny = nz = 0.0
    for i, (x, y, z) in enumerate(points):
        x2, y2, z2 = points[(i + 1) % len(points)]
        nx += (y - y2) * (z + z2)
        ny += (z - z2) * (x + x2)
        nz += (x - x2) * (y + y2)
    return (nx, ny, nz)


def rot_y(p, yaw: float) -> Vec3:
    """Rotates about +Y by `yaw` so local +Z ends up facing (sin yaw, 0, cos yaw)."""
    s, c = math.sin(yaw), math.cos(yaw)
    return (p[0] * c + p[2] * s, p[1], -p[0] * s + p[2] * c)


def planar_uv(points, normal) -> list[tuple[float, float]]:
    """UVs in metres from the face's dominant axis plane (stable across faces)."""
    ax = max(range(3), key=lambda k: abs(normal[k]))
    out = []
    for x, y, z in points:
        if ax == 1:
            out.append((x, -z if normal[1] > 0 else z))
        elif ax == 0:
            out.append((-z if normal[0] > 0 else z, y))
        else:
            out.append((x if normal[2] > 0 else -x, y))
    return out


@dataclass
class Face:
    idx: tuple[int, ...]
    material: str
    uvs: list[tuple[float, float]]
    zone: int = 0
    smooth: bool = False


@dataclass
class Mesh:
    """Polygons with per-face material, zone and UVs."""

    verts: list[Vec3] = field(default_factory=list)
    faces: list[Face] = field(default_factory=list)
    _shared: dict = field(default_factory=dict)
    _groups: int = 0

    # -- building ------------------------------------------------------------

    def group(self) -> int:
        """A new vertex-sharing group for one smooth part."""
        self._groups += 1
        return self._groups

    def v(self, p: Vec3, group: int | None = None) -> int:
        """Adds a vertex; within a group, equal positions share one vertex."""
        if group is not None:
            key = (group, round(p[0], 5), round(p[1], 5), round(p[2], 5))
            got = self._shared.get(key)
            if got is not None:
                return got
            self._shared[key] = len(self.verts)
        self.verts.append((float(p[0]), float(p[1]), float(p[2])))
        return len(self.verts) - 1

    def poly(self, points, material: str, uvs=None, zone: int = 0, group: int | None = None,
             smooth: bool = False) -> None:
        """A flat (or smooth, with `group`) polygon; UVs default to planar."""
        points = [tuple(map(float, p)) for p in points]
        if len(points) < 3 or length(newell(points)) < 1e-9:
            return  # degenerate
        if uvs is None:
            uvs = planar_uv(points, newell(points))
        idx = tuple(self.v(p, group) for p in points)
        if len(set(idx)) < 3:
            return
        self.faces.append(Face(idx, material, list(uvs), zone, smooth))

    def mark(self) -> tuple[int, int]:
        return len(self.verts), len(self.faces)

    def transform(self, mark: tuple[int, int], fn) -> None:
        """Applies `fn(point) -> point` to every vertex added since `mark`.
        UVs are kept, so textures stay put on the transformed part."""
        start = mark[0]
        for i in range(start, len(self.verts)):
            self.verts[i] = tuple(map(float, fn(self.verts[i])))
        self._shared = {k: v for k, v in self._shared.items() if v < start}

    def place(self, mark, yaw: float = 0.0, offset: Vec3 = (0.0, 0.0, 0.0)) -> None:
        """Rotates a part about Y by `yaw`, then moves it by `offset`."""
        self.transform(mark, lambda p: add(rot_y(p, yaw), offset))

    def set_zone(self, mark, zone: int) -> None:
        for f in self.faces[mark[1]:]:
            f.zone = zone

    def extend(self, other: "Mesh") -> None:
        base = len(self.verts)
        self.verts.extend(other.verts)
        for f in other.faces:
            self.faces.append(Face(tuple(i + base for i in f.idx), f.material, list(f.uvs), f.zone, f.smooth))

    # -- queries -------------------------------------------------------------

    def triangle_count(self) -> int:
        return sum(len(f.idx) - 2 for f in self.faces)

    def bounds(self) -> tuple[Vec3, Vec3]:
        used = {i for f in self.faces for i in f.idx}
        pts = [self.verts[i] for i in used] or [(0.0, 0.0, 0.0)]
        lo = tuple(min(p[k] for p in pts) for k in range(3))
        hi = tuple(max(p[k] for p in pts) for k in range(3))
        return lo, hi

    def materials(self) -> list[str]:
        seen: dict[str, None] = {}
        for f in self.faces:
            seen.setdefault(f.material, None)
        return list(seen)

    def zones(self) -> set[int]:
        return {f.zone for f in self.faces}

    def face_points(self, f: Face) -> list[Vec3]:
        return [self.verts[i] for i in f.idx]

    def by_material(self) -> dict[str, "Mesh"]:
        """Splits into one mesh per material (one draw call each)."""
        out: dict[str, Mesh] = {}
        remap: dict[str, dict[int, int]] = {}
        for f in self.faces:
            m = out.setdefault(f.material, Mesh())
            rm = remap.setdefault(f.material, {})
            idx = []
            for i in f.idx:
                if i not in rm:
                    rm[i] = len(m.verts)
                    m.verts.append(self.verts[i])
                idx.append(rm[i])
            m.faces.append(Face(tuple(idx), f.material, list(f.uvs), f.zone, f.smooth))
        return out


# ---------------------------------------------------------------------------
# Primitives. All take the mesh first and return nothing; combine with
# `mark()` / `place()` to position parts.


def box(m: Mesh, lo: Vec3, hi: Vec3, material: str, zone: int = 0, skip=()) -> None:
    """Axis-aligned box; `skip` lists sides to leave out (`"+x"`, `"-y"`...)."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    faces = {
        "+x": [(x1, y0, z1), (x1, y0, z0), (x1, y1, z0), (x1, y1, z1)],
        "-x": [(x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0)],
        "+y": [(x0, y1, z1), (x1, y1, z1), (x1, y1, z0), (x0, y1, z0)],
        "-y": [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],
        "+z": [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
        "-z": [(x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0)],
    }
    for side, pts in faces.items():
        if side not in skip:
            m.poly(pts, material, zone=zone)


def centred_box(m: Mesh, center: Vec3, size: Vec3, material: str, zone: int = 0, skip=()) -> None:
    """Box by its bottom centre and size (x, y, z)."""
    cx, cy, cz = center
    sx, sy, sz = size
    box(m, (cx - sx / 2, cy, cz - sz / 2), (cx + sx / 2, cy + sy, cz + sz / 2), material, zone, skip)


def lathe(m: Mesh, profile, segments: int, material: str, zone: int = 0, center: Vec3 = (0.0, 0.0, 0.0),
          smooth: bool = True, cap_top: bool = True, cap_bottom: bool = False, phase: float = 0.0,
          scale_xz: tuple[float, float] = (1.0, 1.0)) -> None:
    """Revolves a profile `[(radius, y), ...]` (bottom to top) around +Y.

    Rings of zero radius collapse to a point. UVs: u = arc length around the
    ring in metres, v = distance along the profile. `scale_xz` squashes the
    ring into an ellipse (oval tables, flattened lamps).
    """
    g = m.group() if smooth else None
    cx, cy, cz = center
    sx, sz = scale_xz
    angles = [phase + 2 * math.pi * i / segments for i in range(segments + 1)]
    # Distance along the profile for v.
    vs = [0.0]
    for (r0, y0), (r1, y1) in zip(profile, profile[1:]):
        vs.append(vs[-1] + math.hypot(r1 - r0, y1 - y0))

    def pt(r, y, a):
        return (cx + r * math.sin(a) * sx, cy + y, cz + r * math.cos(a) * sz)

    for j in range(len(profile) - 1):
        (r0, y0), (r1, y1) = profile[j], profile[j + 1]
        rmax = max(r0, r1, 1e-3)
        for i in range(segments):
            a0, a1 = angles[i], angles[i + 1]
            # Increasing angle runs from +Z toward +X, so this order faces
            # outward for a profile that goes up.
            pts = [pt(r0, y0, a0), pt(r0, y0, a1), pt(r1, y1, a1), pt(r1, y1, a0)]
            uvs = [(a0 * rmax, vs[j]), (a1 * rmax, vs[j]), (a1 * rmax, vs[j + 1]), (a0 * rmax, vs[j + 1])]
            # Drop collapsed corners (cone tips) so faces stay valid.
            keep = []
            for p, uv in zip(pts, uvs):
                if not keep or length(sub(p, keep[-1][0])) > 1e-7:
                    keep.append((p, uv))
            if len(keep) > 1 and length(sub(keep[0][0], keep[-1][0])) < 1e-7:
                keep.pop()
            if len(keep) >= 3:
                m.poly([p for p, _ in keep], material, [uv for _, uv in keep], zone, g, smooth)
    for (r, y), want, up in ((profile[-1], cap_top, True), (profile[0], cap_bottom, False)):
        if want and r > 1e-6:
            ring = [pt(r, y, a) for a in angles[:-1]]
            if not up:
                ring.reverse()
            m.poly(ring, material, zone=zone)


def cylinder(m: Mesh, base: Vec3, radius: float, height: float, segments: int, material: str, zone: int = 0,
             cap_top: bool = True, cap_bottom: bool = False, smooth: bool = True) -> None:
    lathe(m, [(radius, 0.0), (radius, height)], segments, material, zone, base, smooth, cap_top, cap_bottom)


def rod(m: Mesh, p0: Vec3, p1: Vec3, radius: float, segments: int, material: str, zone: int = 0,
        caps: bool = True) -> None:
    """A cylinder between two points (posts, rails, bars)."""
    axis = sub(p1, p0)
    h = length(axis)
    if h < EPS:
        return
    mk = m.mark()
    cylinder(m, (0.0, 0.0, 0.0), radius, h, segments, material, zone, cap_top=caps, cap_bottom=caps)
    d = normalize(axis)
    # Rotation taking +Y to d (Rodrigues).
    up = (0.0, 1.0, 0.0)
    c = dot(up, d)
    if c > 1 - 1e-9:
        rot = lambda p: p  # noqa: E731
    elif c < -1 + 1e-9:
        rot = lambda p: (p[0], -p[1], -p[2])  # noqa: E731
    else:
        k = normalize(cross(up, d))
        s = math.sqrt(1 - c * c)

        def rot(p):
            kxp = cross(k, p)
            kdp = dot(k, p)
            return tuple(p[i] * c + kxp[i] * s + k[i] * kdp * (1 - c) for i in range(3))

    m.transform(mk, lambda p: add(rot(p), p0))


def prism(m: Mesh, outline, z0: float, z1: float, material: str, zone: int = 0, sides: bool = True,
          front: bool = True, back: bool = True, side_material: str | None = None) -> None:
    """Extrudes a 2D outline `[(x, y), ...]` (counter-clockwise seen from +Z)
    between the planes z = z0 (back) and z = z1 (front)."""
    if front:
        m.poly([(x, y, z1) for x, y in outline], material, zone=zone)
    if back:
        m.poly([(x, y, z0) for x, y in reversed(outline)], material, zone=zone)
    if sides:
        n = len(outline)
        for i in range(n):
            (xa, ya), (xb, yb) = outline[i], outline[(i + 1) % n]
            m.poly([(xa, ya, z0), (xb, yb, z0), (xb, yb, z1), (xa, ya, z1)], side_material or material, zone=zone)


def arch_outline(width: float, spring: float, shape: str, sill: float = 0.0, segments: int = 8):
    """Outline of an opening, counter-clockwise from the bottom left, centred
    on x = 0: `pointed` (two-centred, apex 0.55 x span above the springing
    line, per the style guide), `round` (semicircular), `flat`, or `circle`
    (a circle of diameter `width` whose centre is at `spring`)."""
    hw = width / 2
    if shape == "circle":
        n = segments * 2
        return [(hw * math.cos(-math.pi / 2 + 2 * math.pi * i / n), spring + hw * math.sin(-math.pi / 2 + 2 * math.pi * i / n))
                for i in range(n)]
    pts = [(-hw, sill), (hw, sill)]
    if shape == "flat":
        return pts + [(hw, spring), (-hw, spring)]
    if shape == "round":
        arc = [(hw * math.cos(math.pi * i / segments), spring + hw * math.sin(math.pi * i / segments))
               for i in range(1, segments)]
        return pts + [(hw, spring)] + arc + [(-hw, spring)]
    if shape == "pointed":
        # Two arcs centred on the springing line meet at the apex, 0.55 x the
        # span above it. The right arc passes through the right jamb (hw) and
        # the apex (0, rise), so its centre is at x = c with
        # (hw - c)^2 = c^2 + rise^2.
        rise = 0.55 * width
        c = (hw * hw - rise * rise) / (2 * hw)
        r = hw - c
        apex = math.atan2(rise, -c)
        right = [(c + r * math.cos(apex * i / segments), spring + r * math.sin(apex * i / segments))
                 for i in range(segments + 1)]
        right[-1] = (0.0, spring + rise)
        left = [(-x, y) for x, y in reversed(right[:-1])]
        return pts + right + left
    raise ValueError(f"unknown arch shape {shape!r}")


def polygon_area(outline) -> float:
    a = 0.0
    for i in range(len(outline)):
        (x0, y0), (x1, y1) = outline[i], outline[(i + 1) % len(outline)]
        a += x0 * y1 - x1 * y0
    return a / 2


def offset_convex(outline, d: float):
    """Offsets a convex counter-clockwise outline outward by `d`."""
    n = len(outline)
    out = []
    for i in range(n):
        p_prev, p, p_next = outline[i - 1], outline[i], outline[(i + 1) % n]
        e0 = normalize((p[0] - p_prev[0], p[1] - p_prev[1], 0.0))
        e1 = normalize((p_next[0] - p[0], p_next[1] - p[1], 0.0))
        n0, n1 = (e0[1], -e0[0]), (e1[1], -e1[0])
        nx, ny = n0[0] + n1[0], n0[1] + n1[1]
        ln = math.hypot(nx, ny)
        if ln < 1e-9:
            nx, ny, ln = n0[0], n0[1], 1.0
        nx, ny = nx / ln, ny / ln
        cosh = max(0.3, nx * n0[0] + ny * n0[1])
        out.append((p[0] + nx * d / cosh, p[1] + ny * d / cosh))
    return out


def dedupe_outline(outline, eps: float = 1e-6):
    out = []
    for p in outline:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > eps:
            out.append(p)
    if len(out) > 1 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) <= eps:
        out.pop()
    return out
