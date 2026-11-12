"""Architecture for the dorm room shells (pure Python, builds `meshkit.Mesh`).

A shell is seen only from inside, so every face looks into the room: walls
face the centre, ceilings face down, reveals face into their openings. The
pieces:

* `Facet`: one straight wall segment with openings (windows, doors), each
  cut out with its reveal (depth into the wall), a pane or door leaf at the
  back, an optional architrave on the wall face and a sill board.
* `round_room_facets`: the 12 facets of a round tower room (a 12-gon whose
  cardinal facets are flat, so furniture can hang on them).
* roofs: `cone_roof` (with rafters), `dome` (ribbed), `barrel_vault`.

Facet-local coordinates: u runs along the wall (left to right seen from the
room), v up, w toward the room. (t, up, n) is right-handed, so polygons that
are counter-clockwise in (u, v) face the room.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import meshkit as mk
from meshkit import Mesh, Vec3

UP = (0.0, 1.0, 0.0)


def direction(theta_deg: float) -> Vec3:
    """Horizontal unit vector for a compass angle: 0 = north (-z), 90 = east (+x)."""
    a = math.radians(theta_deg)
    return (math.sin(a), 0.0, -math.cos(a))


def yaw_facing(vec: Vec3) -> float:
    """Yaw (radians, 0 faces +Z) of a horizontal direction."""
    return math.atan2(vec[0], vec[2])


def orient_toward(points, target: Vec3):
    """`points` wound so the face's normal points toward `target`."""
    n = mk.newell(points)
    c = tuple(sum(p[k] for p in points) / len(points) for k in range(3))
    if mk.dot(n, mk.sub(target, c)) < 0:
        return list(reversed(points))
    return list(points)


@dataclass
class Opening:
    """A hole in a facet. `shape`: pointed / round / flat / circle (see
    `meshkit.arch_outline`; for a circle `spring` is the centre height)."""

    kind: str  # "window" or "door"
    shape: str
    width: float
    sill: float
    spring: float
    depth: float
    offset: float = 0.0
    pane: str = "glass"
    pane_uv: str = "fit"  # "fit": 0..1 over the opening (painted views); "metres"
    frame: str | None = "trim"
    frame_width: float = 0.12
    frame_proud: float = 0.04
    sill_board: str | None = None
    reveal: str | None = None  # reveal material (default: the wall's)

    def outline(self, uc: float):
        pts = mk.arch_outline(self.width, self.spring, self.shape, self.sill)
        return mk.dedupe_outline([(uc + x, y) for x, y in pts])


def _span_at(outline, u: float) -> tuple[float, float] | None:
    """(lowest, highest) v where the vertical line at u crosses a convex outline."""
    vs = []
    n = len(outline)
    for i in range(n):
        (ua, va), (ub, vb) = outline[i], outline[(i + 1) % n]
        lo, hi = min(ua, ub), max(ua, ub)
        if lo - 1e-9 <= u <= hi + 1e-9:
            if abs(ub - ua) < 1e-9:
                vs += [va, vb]
            else:
                t = (u - ua) / (ub - ua)
                vs.append(va + (vb - va) * t)
    return (min(vs), max(vs)) if vs else None


@dataclass
class Facet:
    """A straight wall from `p0` to `p1` (at floor level), facing the room
    along `normal`, from `bottom` to `top` (top may follow a profile)."""

    p0: Vec3
    p1: Vec3
    normal: Vec3
    bottom: float
    top: float
    openings: list[Opening] = field(default_factory=list)

    def __post_init__(self):
        n = mk.normalize(self.normal)
        t = mk.cross(UP, n)
        if mk.dot(t, mk.sub(self.p1, self.p0)) < 0:
            self.p0, self.p1 = self.p1, self.p0
        self.n = n
        self.t = mk.normalize(mk.sub(self.p1, self.p0))
        self.length = mk.length(mk.sub(self.p1, self.p0))

    def at(self, u: float, v: float, w: float = 0.0) -> Vec3:
        p = mk.add(self.p0, mk.scale(self.t, u))
        p = mk.add(p, (0.0, v, 0.0))
        return mk.add(p, mk.scale(self.n, w))

    @property
    def centre(self) -> float:
        return self.length / 2

    def local_box(self, m: Mesh, u0, u1, v0, v1, w0, w1, material: str, skip=()) -> None:
        """A box in facet coordinates (u0..u1, v0..v1, w0..w1)."""
        mark = m.mark()
        mk.box(m, (u0, v0, w0), (u1, v1, w1), material, skip=skip)
        # Local box axes x, y, z -> u, v, w: (t, up, n) is right-handed, so
        # the windings survive.
        m.transform(mark, lambda p: self.at(p[0], p[1], p[2]))

    def build(self, m: Mesh, wall: str, uv_scale: float = 1.0, u_offset: float = 0.0) -> None:
        """Wall face with holes, then each opening's reveal, pane and trims.
        UVs are metres along the wall (u + u_offset) and up."""
        L, H0, H1 = self.length, self.bottom, self.top

        def quad(pts_uv):
            pts = [self.at(u, v) for u, v in pts_uv]
            uvs = [((u + u_offset) * uv_scale, v * uv_scale) for u, v in pts_uv]
            m.poly(pts, wall, uvs)

        holes = sorted(((o, o.outline(self.centre + o.offset)) for o in self.openings),
                       key=lambda h: min(p[0] for p in h[1]))
        u = 0.0
        for o, outline in holes:
            ua, ub = min(p[0] for p in outline), max(p[0] for p in outline)
            if ua > u + 1e-6:
                quad([(u, H0), (ua, H0), (ua, H1), (u, H1)])
            cuts = sorted({p[0] for p in outline})
            for a, b in zip(cuts, cuts[1:]):
                if b - a < 1e-6:
                    continue
                sa, sb = _span_at(outline, a + 1e-7), _span_at(outline, b - 1e-7)
                if sa[0] > H0 + 1e-6 or sb[0] > H0 + 1e-6:
                    quad([(a, H0), (b, H0), (b, sb[0]), (a, sa[0])])
                if sa[1] < H1 - 1e-6 or sb[1] < H1 - 1e-6:
                    quad([(a, sa[1]), (b, sb[1]), (b, H1), (a, H1)])
            u = ub
        if L > u + 1e-6:
            quad([(u, H0), (L, H0), (L, H1), (u, H1)])
        for o, outline in holes:
            self._opening(m, o, outline, wall, uv_scale)

    def _opening(self, m: Mesh, o: Opening, outline, wall: str, uv_scale: float) -> None:
        n = len(outline)
        cu = sum(p[0] for p in outline) / n
        cv = sum(p[1] for p in outline) / n
        front = o.frame_proud if o.frame else 0.0
        hole_centre = self.at(cu, cv, -o.depth / 2)
        reveal_mat = o.reveal or wall
        for i in range(n):
            (ua, va), (ub, vb) = outline[i], outline[(i + 1) % n]
            pts = [self.at(ua, va, front), self.at(ub, vb, front), self.at(ub, vb, -o.depth), self.at(ua, va, -o.depth)]
            pts = orient_toward(pts, hole_centre)
            along = math.hypot(ub - ua, vb - va)
            uvs = None
            if mk.length(mk.newell(pts)) > 1e-9:
                d = o.depth + front
                uvs = [(0.0, 0.0), (along * uv_scale, 0.0), (along * uv_scale, d * uv_scale), (0.0, d * uv_scale)]
                uvs = uvs if pts[0] == self.at(ua, va, front) else uvs[::-1]
            m.poly(pts, reveal_mat, uvs)
        # Pane (glass, painted view or door leaf) at the back, facing the room.
        back = [self.at(u, v, -o.depth) for u, v in outline]
        u0, u1 = min(p[0] for p in outline), max(p[0] for p in outline)
        v0, v1 = min(p[1] for p in outline), max(p[1] for p in outline)
        if o.pane_uv == "fit":
            uvs = [((u - u0) / (u1 - u0), (v - v0) / (v1 - v0)) for u, v in outline]
        else:
            uvs = [(u * uv_scale, v * uv_scale) for u, v in outline]
        m.poly(back, o.pane, uvs)
        if o.frame:
            self._frame(m, o, outline, hole_centre)
        if o.sill_board and o.kind == "window":
            self.local_box(m, u0 - 0.08, u1 + 0.08, o.sill - 0.07, o.sill, -o.depth, 0.14, o.sill_board, skip=("-y",))

    def _frame(self, m: Mesh, o: Opening, outline, hole_centre: Vec3) -> None:
        """An architrave around the opening, standing proud of the wall."""
        outer = mk.offset_convex(outline, o.frame_width)
        f = o.frame_proud
        n = len(outline)
        for i in range(n):
            j = (i + 1) % n
            (ua, va), (ub, vb) = outline[i], outline[j]
            if o.kind == "door" and va < self.bottom + 1e-6 and vb < self.bottom + 1e-6:
                continue  # no trim along the threshold
            (oa_u, oa_v), (ob_u, ob_v) = outer[i], outer[j]
            oa_v, ob_v = max(oa_v, self.bottom), max(ob_v, self.bottom)
            ring = [self.at(ua, va, f), self.at(ub, vb, f), self.at(ob_u, ob_v, f), self.at(oa_u, oa_v, f)]
            m.poly(orient_toward(ring, mk.add(mk.scale(mk.add(ring[0], ring[2]), 0.5), mk.scale(self.n, 1.0))), o.frame)
            side = [self.at(oa_u, oa_v, 0.0), self.at(ob_u, ob_v, 0.0), self.at(ob_u, ob_v, f), self.at(oa_u, oa_v, f)]
            mid = mk.scale(mk.add(side[0], side[2]), 0.5)
            m.poly(orient_toward(side, mk.add(mid, mk.sub(mid, hole_centre))), o.frame)


def round_room_facets(apothem: float, height: float, segments: int = 12) -> list[tuple[float, Facet]]:
    """Facets of a regular polygon room centred on the origin, one per
    `360 / segments` degrees starting with a flat facet facing north."""
    half = 180.0 / segments
    r = apothem / math.cos(math.radians(half))
    out = []
    for k in range(segments):
        theta = k * 360.0 / segments
        p0 = mk.scale(direction(theta - half), r)
        p1 = mk.scale(direction(theta + half), r)
        out.append((theta, Facet(p0, p1, mk.scale(direction(theta), -1.0), 0.0, height)))
    return out


def polygon_floor(m: Mesh, corners, y: float, material: str, uv_scale: float = 1.0) -> None:
    pts = [(c[0], y, c[2]) for c in corners]
    pts = orient_toward(pts, (0.0, y + 10.0, 0.0))
    m.poly(pts, material, [(p[0] * uv_scale, -p[2] * uv_scale) for p in pts])


def cone_roof(m: Mesh, corners, eave: float, apex: float, material: str, uv_scale: float = 1.0) -> None:
    """A cone (pyramid over the corner polygon) seen from below."""
    top = (0.0, apex, 0.0)
    n = len(corners)
    for i in range(n):
        a, b = corners[i], corners[(i + 1) % n]
        pa, pb = (a[0], eave, a[2]), (b[0], eave, b[2])
        tri = orient_toward([pa, pb, top], (0.0, 0.0, 0.0))
        slant = mk.length(mk.sub(top, mk.scale(mk.add(pa, pb), 0.5)))
        w = mk.length(mk.sub(pb, pa))
        uvs = {pa: (0.0, 0.0), pb: (w * uv_scale, 0.0), top: (w * uv_scale / 2, slant * uv_scale)}
        m.poly(tri, material, [uvs[p] for p in tri])


def dome(m: Mesh, corners, base: float, rise: float, rings: int, material: str, repeats: float = 3.0) -> None:
    """A dome over the corner polygon (rings shrink with cos, rise with sin).
    UVs run once around per `repeats` and from the base (v 0) to the crown (v 1)."""
    n = len(corners)
    lvls = []
    for i in range(rings + 1):
        phi = (math.pi / 2) * i / rings
        lvls.append((math.cos(phi), base + rise * math.sin(phi), i / rings))
    for i in range(rings):
        (s0, y0, v0), (s1, y1, v1) = lvls[i], lvls[i + 1]
        for k in range(n):
            a, b = corners[k], corners[(k + 1) % n]
            pts = [(a[0] * s0, y0, a[2] * s0), (b[0] * s0, y0, b[2] * s0), (b[0] * s1, y1, b[2] * s1), (a[0] * s1, y1, a[2] * s1)]
            ua, ub = k / n * repeats, (k + 1) / n * repeats
            uvs = [(ua, v0), (ub, v0), (ub, v1), (ua, v1)]
            if i == rings - 1:
                pts, uvs = pts[:3], uvs[:3]  # the crown collapses to a point
                pts[2] = (0.0, y1, 0.0)
            oriented = orient_toward(pts, (0.0, base - 5.0, 0.0))
            if oriented[0] != pts[0]:
                uvs = uvs[::-1]
            m.poly(oriented, material, uvs)


def barrel_vault(m: Mesh, x0: float, x1: float, half_span: float, spring: float, crown: float, segments: int,
                 material: str, uv_scale: float = 1.0) -> list[tuple[float, float]]:
    """A segmental vault along x over z in [-half_span, half_span]; returns
    its arc as [(z, y)] (left to right) for walls and ribs to follow."""
    sag = crown - spring
    r = (half_span**2 + sag**2) / (2 * sag)
    cy = crown - r
    a0 = math.atan2(spring - cy, -half_span)
    a1 = math.atan2(spring - cy, half_span)
    arc = []
    for i in range(segments + 1):
        a = a0 + (a1 - a0) * i / segments
        arc.append((r * math.cos(a), cy + r * math.sin(a)))
    s = 0.0
    for (za, ya), (zb, yb) in zip(arc, arc[1:]):
        step = math.hypot(zb - za, yb - ya)
        pts = [(x0, ya, za), (x1, ya, za), (x1, yb, zb), (x0, yb, zb)]
        uvs = [(x0 * uv_scale, s * uv_scale), (x1 * uv_scale, s * uv_scale), (x1 * uv_scale, (s + step) * uv_scale),
               (x0 * uv_scale, (s + step) * uv_scale)]
        o = orient_toward(pts, ((x0 + x1) / 2, 0.0, 0.0))
        m.poly(o, material, uvs if o[0] == pts[0] else uvs[::-1])
        s += step
    return arc


def rafter(m: Mesh, a: Vec3, b: Vec3, width: float, material: str) -> None:
    """A square beam from a to b (four sides, no ends)."""
    mk.rod(m, a, b, width * 0.7071, 4, material, caps=False)
