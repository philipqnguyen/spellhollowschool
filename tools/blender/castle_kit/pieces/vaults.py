"""Vaults (castle kit M2): the Entrance Hall's lierne vault bays
and the fan vault over the stair head. Hero pieces: frame as props.json
`kit`, the origin at the bay's centre on the springing line, local x across
the hall, z along it, y up (0 = the springing, `rise` = the apex).

Both are height fields over their plan rectangle (`LierneSurface.height`,
`FanSurface.height`), so ribs are plan polylines lifted onto the surface
and swept with the `rib` trim strip; webs are `castle_ceiling` with world
UVs (castle_kit/grid.py world_uv).

A lierne bay `kit_vault_lierne_bay_<w>x<h>` is a domical cross vault: an
elliptical transverse arch across the hall at each bay line (z = +-h/2,
shared with the next bay: each bay draws its half of the transverse rib), a
pointed wall arch (formeret, rise grid.ARCH_RISE x h) on each side wall, so
every bay has a lunette on both walls for its windows, and four cells
between them meeting on the diagonals at the central boss (`rise`). Ribs:
diagonals, ridges, tiercerons from the corners to the ridges, a star of
liernes, wall ribs and bosses at the crossings. `gap` leaves a centred
strip |x| < gap / 2 open for a fan vault.

The fan vault `kit_vault_fan_<w>x<h>` fills such a gap: quarter conoids
springing from the wall at its two corners at z = -h/2 (the north wall),
pendants hanging at its two corners at z = +h/2, a flat central panel at the
apex with a boss; curtains close it against the lierne bay around it (whose
surface it reads through `LierneSurface`), edge ribs cover the joins.

`meta["collider"]` holds a coarse shell of each vault for the camera
collider (assemble.py), never drawn: camera.ts keeps 0.8 m of relief from
ceiling faces and the vault hangs up to 6 m below the layout ceiling."""

from __future__ import annotations

import math

from castle_kit import grid as G
from castle_kit.pieces import geom
from castle_kit.pieces.geom import Piece, add_face

WEB = "castle_ceiling|world"
RIB = "castle_trim|trim:rib"
BOSS = "castle_trim|fit:boss"
# Rib profiles (a across the rib, b along the surface's up: b < 0 hangs into
# the room, b > 0 is buried in the web so chords never open a gap).
RIB_PROFILE = [(-0.14, 0.06), (-0.14, -0.06), (-0.06, -0.2), (0.06, -0.2), (0.14, -0.06), (0.14, 0.06)]
HALF_PROFILE = [(0.0, -0.2), (0.06, -0.2), (0.14, -0.06), (0.14, 0.06)]
RIB_DEPTH = 0.2
DEFAULT_PENDANT = 1.5  # a fan vault's pendants hang to this above the springing


def _unit(v):
    return geom.unit(v)


def _cross(a, b):
    return geom.cross(a, b)


def _sc(v, k):
    return (v[0] * k, v[1] * k, v[2] * k)


def _lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(len(a)))


# -- surfaces -----------------------------------------------------------------------


class LierneSurface:
    """The web of a lierne bay `w` x `h` rising `rise` (local frame)."""

    def __init__(self, w: float = 32.0, h: float = 10.0, rise: float = 6.0):
        self.hx, self.hz, self.rise = w / 2.0, h / 2.0, rise
        self.formeret = min(G.ARCH_RISE * h, rise - 0.3)

    def transverse(self, x: float) -> float:
        """The transverse arch (an ellipse across the hall) at x."""
        q = min(1.0, abs(x) / self.hx)
        return self.rise * math.sqrt(max(0.0, 1.0 - q * q))

    def wall(self, z: float) -> float:
        """The pointed wall arch (formeret) at z."""
        return G.arch_height(2 * self.hz, self.formeret, max(-self.hz, min(self.hz, z))) if self.formeret > 0 else 0.0

    @staticmethod
    def g(u: float) -> float:
        return math.sin(math.pi / 2 * min(1.0, max(0.0, u)))

    def height(self, x: float, z: float) -> float:
        x = max(-self.hx, min(self.hx, x))
        z = max(-self.hz, min(self.hz, z))
        ax, az = abs(x) / self.hx, abs(z) / self.hz
        if az >= ax:
            u = 1.0 - az
            if u >= 1.0 - 1e-12:
                return self.rise
            b = self.transverse(x / (1.0 - u))
        else:
            u = 1.0 - ax
            b = self.wall(z / (1.0 - u) if u < 1.0 - 1e-12 else 0.0)
        return b + (self.rise - b) * self.g(u)

    def point(self, x: float, z: float, lift: float = 0.0):
        return (x, self.height(x, z) + lift, z)

    def up(self, x: float, z: float, e: float = 0.03):
        return _surface_up(self.height, x, z, e, self.hx, self.hz)


class FanSurface:
    """A fan vault `w` x `h` rising `rise`: quarter conoids springing at the
    corners z = -h/2 (from 0) and hanging from pendants at z = +h/2 (from
    `pendant`), radius min(w, h) / 2, a flat panel at `rise` between them."""

    def __init__(self, w: float = 12.0, h: float = 10.0, rise: float = 6.0, pendant: float = DEFAULT_PENDANT):
        self.hx, self.hz, self.rise, self.pendant = w / 2.0, h / 2.0, rise, pendant
        self.r = min(self.hx, self.hz)
        self.corners = [(-self.hx, -self.hz, 0.0), (self.hx, -self.hz, 0.0), (-self.hx, self.hz, pendant),
                        (self.hx, self.hz, pendant)]

    def profile(self, base: float, rho: float) -> float:
        """A conoid's height at rho = r / R from its axis (vertical at the
        axis, level at the rim)."""
        q = min(1.0, max(0.0, rho))
        return base + (self.rise - base) * math.sqrt(max(0.0, 1.0 - (1.0 - q) ** 2))

    def height(self, x: float, z: float) -> float:
        x = max(-self.hx, min(self.hx, x))
        z = max(-self.hz, min(self.hz, z))
        for cx, cz, base in self.corners:
            r = math.hypot(x - cx, z - cz)
            if r <= self.r:
                return self.profile(base, r / self.r)
        return self.rise

    def up(self, x: float, z: float, e: float = 0.03):
        return _surface_up(self.height, x, z, e, self.hx, self.hz)


def _surface_up(height, x, z, e, hx, hz):
    """The upward normal of a height field (into the vault mass), by
    differences that stay inside the plan rectangle."""
    x0, x1 = max(-hx, x - e), min(hx, x + e)
    z0, z1 = max(-hz, z - e), min(hz, z + e)
    dx = (height(x1, z) - height(x0, z)) / max(1e-9, x1 - x0)
    dz = (height(x, z1) - height(x, z0)) / max(1e-9, z1 - z0)
    return _unit((-dx, 1.0, -dz))


# -- sweeps on a surface ----------------------------------------------------------------


def rib_sweep(faces, pts3, ups, profile=RIB_PROFILE, across=None, side: float = 1.0, tag: str = RIB,
              p_range=(0.0, 0.3)) -> None:
    """Sweeps a rib profile along a 3D polyline whose points lie on a
    surface with the given up vectors. `across` fixes the profile's a axis
    (a boundary plane's normal), else it is tangent x up. `side` mirrors
    the profile (half ribs). UVs on the `rib` strip: s = metres along, p
    across the profile within `p_range`."""
    n = len(pts3)
    if n < 2:
        return
    prof = [(a * side, b) for a, b in profile]
    lens = geom.profile_lengths(prof)
    tot = lens[-1] or 1.0
    p0, p1 = p_range
    rings, frames = [], []
    s_acc = [0.0]
    for i in range(1, n):
        s_acc.append(s_acc[-1] + geom.norm(geom.sub(pts3[i], pts3[i - 1])))
    for i in range(n):
        a = pts3[max(0, i - 1)]
        b = pts3[min(n - 1, i + 1)]
        t = _unit(geom.sub(b, a))
        up = ups[i]
        if across is not None:
            ac = _unit(across)
            u2 = _unit(_cross(ac, t))
            if geom.dot(u2, up) < 0:
                u2 = (-u2[0], -u2[1], -u2[2])
        else:
            ac = _unit(_cross(t, up))
            u2 = _unit(_cross(ac, t))
            if geom.dot(u2, up) < 0:
                u2 = (-u2[0], -u2[1], -u2[2])
        frames.append((ac, u2))
        rings.append([geom.add(pts3[i], geom.add(_sc(ac, pa), _sc(u2, pb))) for pa, pb in prof])
    for i in range(n - 1):
        for j in range(len(prof) - 1):
            (a0, b0), (a1, b1) = prof[j], prof[j + 1]
            na, nb = (b1 - b0), -(a1 - a0)
            if side < 0:
                na, nb = -na, -nb
            ac, u2 = frames[i]
            nrm = geom.add(_sc(ac, na), _sc(u2, nb))
            q0 = p0 + (p1 - p0) * lens[j] / tot
            q1 = p0 + (p1 - p0) * lens[j + 1] / tot
            quad = [rings[i][j], rings[i + 1][j], rings[i + 1][j + 1], rings[i][j + 1]]
            uv = [(s_acc[i], q0), (s_acc[i + 1], q0), (s_acc[i + 1], q1), (s_acc[i], q1)]
            add_face(faces, quad, tag, uv, nrm)


def boss_at(faces, centre, up, size: float, big: bool = False) -> None:
    """An octagonal carved boss hanging from a surface point along -up
    (buried 0.1 into the web)."""
    up = _unit(up)
    ref = (0.0, 0.0, 1.0) if abs(up[2]) < 0.9 else (1.0, 0.0, 0.0)
    e1 = _unit(_cross(up, ref))
    e2 = _unit(_cross(e1, up))
    r = size / 2.0
    if big:
        tiers = [(0.1, r), (-0.12, r), (-0.2, r * 0.86), (-0.3, r * 0.6), (-0.38, r * 0.3), (-0.44, 0.0)]
    else:
        tiers = [(0.1, r), (-0.12, r), (-0.26, r * 0.6), (-0.34, 0.0)]
    n = 8

    def ring(b, rr):
        out = []
        for i in range(n):
            a = math.pi / n + 2 * math.pi * i / n
            d = geom.add(_sc(e1, rr * math.cos(a)), _sc(e2, rr * math.sin(a)))
            out.append(geom.add(centre, geom.add(d, _sc(up, b))))
        return out

    for k, ((b0, r0), (b1, r1)) in enumerate(zip(tiers, tiers[1:])):
        ra, rb = ring(b0, r0), ring(b1, r1)
        v0, v1 = 1.0 - k / (len(tiers) - 1), 1.0 - (k + 1) / (len(tiers) - 1)
        for i in range(n):
            j = (i + 1) % n
            quad = [ra[i], ra[j], rb[j], rb[i]]
            mid = _sc(geom.add(geom.sub(ra[i], centre), geom.sub(ra[j], centre)), 0.5)
            nrm = geom.add(mid, _sc(up, -0.5))
            add_face(faces, quad, BOSS, [(i / n, v0), ((i + 1) / n, v0), ((i + 1) / n, v1), (i / n, v1)], nrm)


# -- plan helpers --------------------------------------------------------------------------


def _clip_half(poly, keep_pos: bool, g: float):
    """The part of a polygon (3D, plan x) with x >= g (keep_pos) or x <= -g."""
    out = []
    m = len(poly)
    for i in range(m):
        a, b = poly[i], poly[(i + 1) % m]
        da = (a[0] - g) if keep_pos else (-g - a[0])
        db = (b[0] - g) if keep_pos else (-g - b[0])
        if da >= -1e-9:
            out.append(a)
        if (da >= -1e-9) != (db >= -1e-9):
            t = da / (da - db)
            out.append(tuple(a[k] + (b[k] - a[k]) * t for k in range(3)))
    return out


def _gap_clip(poly, gap: float):
    """Polygons of `poly` outside the centred strip |x| < gap / 2."""
    if gap <= 0:
        return [poly]
    g = gap / 2.0
    xs = [p[0] for p in poly]
    if min(xs) >= g - 1e-9 or max(xs) <= -g + 1e-9:
        return [poly]
    out = []
    for keep in (True, False):
        part = _clip_half(poly, keep, g)
        if len(part) >= 3:
            out.append(part)
    return out


def _gap_polylines(plan_pts, gap: float):
    """A plan polyline cut where it enters the strip |x| < gap / 2."""
    if gap <= 0:
        return [plan_pts]
    g = gap / 2.0

    def inside(p):
        return abs(p[0]) < g - 1e-9

    def cross(q, p, edge):
        t = (edge - q[0]) / (p[0] - q[0])
        return (edge, q[1] + (p[1] - q[1]) * t)

    out, cur = [], []
    for i, p in enumerate(plan_pts):
        if i > 0:
            q = plan_pts[i - 1]
            if inside(q) != inside(p):
                outer = q if inside(p) else p
                c = cross(q, p, g if outer[0] > 0 else -g)
                if inside(p):
                    cur.append(c)
                    if len(cur) >= 2:
                        out.append(cur)
                    cur = []
                else:
                    cur = [c]
            elif not inside(p) and (q[0] > 0) != (p[0] > 0) and abs(p[0] - q[0]) > 1e-12:
                # A segment jumping over the whole strip.
                cur.append(cross(q, p, g if q[0] > 0 else -g))
                if len(cur) >= 2:
                    out.append(cur)
                cur = [cross(q, p, g if p[0] > 0 else -g)]
        if not inside(p):
            cur.append(p)
    if len(cur) >= 2:
        out.append(cur)
    return out


def _line(a, b, n: int):
    return [_lerp(a, b, k / n) for k in range(n + 1)]


def _sweep_on(faces, surf, plan_pts, gap=0.0, profile=RIB_PROFILE, across=None, side=1.0, p_range=(0.0, 0.3),
              lift: float = 0.0):
    for part in _gap_polylines(plan_pts, gap):
        pts3 = [(x, surf.height(x, z) + lift, z) for x, z in part]
        ups = [surf.up(x, z) for x, z in part]
        rib_sweep(faces, pts3, ups, profile, across, side, RIB, p_range)


def _tris_down(out, a, b, c) -> None:
    """A web triangle facing down (into the room)."""
    add_face(out, [a, b, c], WEB, None, (0.0, -1.0, 0.0))


# -- the lierne bay ----------------------------------------------------------------------


def lierne_bay(w: float, h: float, rise: float = 6.0, gap: float = 0.0, ns: int = 14, nt: int = 8, nu: int = 6) -> Piece:
    """`kit_vault_lierne_bay_<w>x<h>`: one bay of the lierne vault (see the
    module docstring); <= 3,000 triangles at 32 x 10."""
    if rise <= 0 or gap < 0 or gap >= w:
        raise ValueError("a lierne bay needs rise > 0 and 0 <= gap < w")
    p = Piece("")
    f = p.faces
    S = LierneSurface(w, h, rise)
    hx, hz = S.hx, S.hz

    def cell_pt(kind, a, u, sgn):
        """Plan point of a cell: kind 'ns' (a in -1..1 along x, sgn: -1 north, +1 south) or 'we'
        (a along z, sgn: -1 west, +1 east); u from the boundary arch (0) to the centre (1)."""
        if kind == "ns":
            return ((1 - u) * a * hx, (1 - u) * sgn * hz)
        return ((1 - u) * sgn * hx, (1 - u) * a * hz)

    def spaced(n):
        return [math.sin(-math.pi / 2 + math.pi * k / n) for k in range(n + 1)]

    # Webs: four cells on grids that follow the diagonals (the groins).
    for kind, sgn, n_a in (("ns", -1.0, ns), ("ns", 1.0, ns), ("we", -1.0, nt), ("we", 1.0, nt)):
        a_vals = spaced(n_a)
        for i in range(n_a):
            for k in range(nu):
                u0, u1 = k / nu, (k + 1) / nu
                q = [cell_pt(kind, a_vals[i], u0, sgn), cell_pt(kind, a_vals[i + 1], u0, sgn),
                     cell_pt(kind, a_vals[i + 1], u1, sgn), cell_pt(kind, a_vals[i], u1, sgn)]
                pts = [S.point(x, z) for x, z in q]
                for tri in ((pts[0], pts[1], pts[2]), (pts[0], pts[2], pts[3])):
                    for part in _gap_clip(list(tri), gap):
                        add_face(f, part, WEB, None, (0.0, -1.0, 0.0))
    # Ribs.
    corners = [(-hx, -hz), (hx, -hz), (-hx, hz), (hx, hz)]
    centre = (0.0, 0.0)
    for c in corners:  # diagonals
        _sweep_on(f, S, _line(c, centre, 10), gap)
    for zs in (-hz, hz):  # this bay's half of each transverse rib (inside the bay)
        xs = [hx * v for v in spaced(16)]
        _sweep_on(f, S, [(x, zs) for x in xs], gap, HALF_PROFILE, (0.0, 0.0, 1.0), -math.copysign(1.0, zs), (0.15, 0.3))
    for xs in (-hx, hx):  # wall ribs along the formerets (stopping short of the corners the next bay shares)
        zs_ = [hz * 0.97 * v for v in spaced(10)]
        _sweep_on(f, S, [(xs, z) for z in zs_], gap, HALF_PROFILE, (1.0, 0.0, 0.0), -math.copysign(1.0, xs),
                  (0.15, 0.3))
    ridge = [((0.0, -hz), centre), ((0.0, hz), centre), ((-hx, 0.0), centre), ((hx, 0.0), centre)]
    for a, b in ridge:
        _sweep_on(f, S, _line(a, b, 6 if abs(a[0]) > 0 else 4), gap)
    # Tiercerons: from each corner to the ridges at half way.
    mid_ns = [(0.0, -hz / 2), (0.0, hz / 2)]
    mid_we = [(-hx / 2, 0.0), (hx / 2, 0.0)]
    for cx, cz in corners:
        tz = (0.0, math.copysign(hz / 2, cz))
        tx = (math.copysign(hx / 2, cx), 0.0)
        _sweep_on(f, S, _line((cx, cz), tz, 6), gap)
        _sweep_on(f, S, _line((cx, cz), tx, 6), gap)
    # Liernes: a star joining the ridge points through the diagonals at 0.6.
    diag = [(cx * 0.4, cz * 0.4) for cx, cz in corners]
    for dx_, dz_ in diag:
        _sweep_on(f, S, _line((0.0, math.copysign(hz / 2, dz_)), (dx_, dz_), 3), gap)
        _sweep_on(f, S, _line((math.copysign(hx / 2, dx_), 0.0), (dx_, dz_), 3), gap)
    # Bosses: the centre, the ridge points, the lierne crossings, the transverse crown (low z only).
    spots = [(centre, 0.9, True)] + [(m, 0.6, False) for m in mid_ns + mid_we] + [(d, 0.5, False) for d in diag]
    spots.append(((0.0, -hz), 0.6, False))
    for (x, z), size, big in spots:
        if gap > 0 and abs(x) < gap / 2 + size / 2:
            continue
        boss_at(f, S.point(x, z), S.up(x, z), size, big)
    p.meta.update(rise=rise, gap=gap, formeret=S.formeret, collider=_lierne_collider(S, gap))
    p.meta["surface"] = ("lierne", w, h, rise)
    return p


def _lierne_collider(S: LierneSurface, gap: float):
    """A coarse shell of the bay's web (each cell 4 x 2, gap left open)."""
    out = []
    hx, hz = S.hx, S.hz
    for kind, sgn, n_a in (("ns", -1.0, 4), ("ns", 1.0, 4), ("we", -1.0, 2), ("we", 1.0, 2)):
        for i in range(n_a):
            for k in range(2):
                a0, a1 = -1 + 2 * i / n_a, -1 + 2 * (i + 1) / n_a
                u0, u1 = k / 2, (k + 1) / 2
                if kind == "ns":
                    q = [((1 - u) * a * hx, (1 - u) * sgn * hz) for a, u in ((a0, u0), (a1, u0), (a1, u1), (a0, u1))]
                else:
                    q = [((1 - u) * sgn * hx, (1 - u) * a * hz) for a, u in ((a0, u0), (a1, u0), (a1, u1), (a0, u1))]
                pts = [S.point(x, z) for x, z in q]
                for tri in ((pts[0], pts[1], pts[2]), (pts[0], pts[2], pts[3])):
                    if geom.norm(geom.newell(list(tri))) < 1e-9:
                        continue
                    out.extend(_gap_clip(list(tri), gap))
    return out


# -- the fan vault --------------------------------------------------------------------------


def fan_vault(w: float, h: float, rise: float = 6.0, pendant: float = DEFAULT_PENDANT, bay_w: float = 32.0,
              bay_rise: float | None = None) -> Piece:
    """`kit_vault_fan_<w>x<h>`: the fan vault filling a lierne bay's gap
    (the bay `bay_w` wide and `h` deep, rising `bay_rise`, default `rise`);
    <= 6,000 triangles at 12 x 10."""
    p = Piece("")
    f = p.faces
    F = FanSurface(w, h, rise, pendant)
    L = LierneSurface(bay_w, h, rise if bay_rise is None else bay_rise)
    hx, hz, R = F.hx, F.hz, F.r
    nr, nth = 6, 12
    # Conoid webs: a polar grid per corner, over the quarter toward the bay's middle.
    for cx, cz, base in F.corners:
        for i in range(nth):
            for k in range(nr):
                pts = []
                for a_i, r_k in ((i, k), (i + 1, k), (i + 1, k + 1), (i, k + 1)):
                    th = (a_i / nth) * math.pi / 2
                    r = R * (1 - math.cos(math.pi / 2 * r_k / nr))
                    dx, dz = _quarter_dir(cx, cz, th)
                    x, z = cx + r * dx, cz + r * dz
                    pts.append((x, F.profile(base, r / R), z))
                for tri in ((pts[0], pts[1], pts[2]), (pts[0], pts[2], pts[3])):
                    add_face(f, list(tri), WEB, None, (0.0, -1.0, 0.0))
    # The central panel at the apex: the rectangle less the four quarter discs
    # (its arcs on the conoids' own rim points: no slivers between them).
    add_face(f, [(x, rise, z) for x, z in _panel_outline(hx, hz, R, nth)], WEB, None, (0.0, -1.0, 0.0))
    # Curtains against the lierne bay: x = +-hx (facing out) and z = +hz.
    for sx in (-1.0, 1.0):
        zs = [-hz + 2 * hz * k / 20 for k in range(21)]
        _curtain(f, [(sx * hx, z) for z in zs], F.height, L.height, (sx, 0.0, 0.0))
    xs = [-hx + 2 * hx * k / 20 for k in range(21)]
    _curtain(f, [(x, hz) for x in xs], F.height, L.height, (0.0, 0.0, 1.0))
    # Ribs: radial ribs and two rings on every conoid, edge ribs on the joins.
    for cx, cz, base in F.corners:
        for j in range(1, 6):
            th = j / 6 * math.pi / 2
            dx, dz = _quarter_dir(cx, cz, th)
            plan = [(cx + R * (1 - math.cos(math.pi / 2 * k / 6)) * dx, cz + R * (1 - math.cos(math.pi / 2 * k / 6)) * dz)
                    for k in range(1, 7)]
            _sweep_on(f, F, plan)
        for frac, n in ((1.0, 8), (0.55, 6)):
            plan = []
            for k in range(n + 1):
                dx, dz = _quarter_dir(cx, cz, k / n * math.pi / 2)
                plan.append((cx + R * frac * dx, cz + R * frac * dz))
            _sweep_on(f, F, plan)
    for sx in (-1.0, 1.0):
        plan = [(sx * hx, -hz + 2 * hz * k / 16) for k in range(17)]
        _sweep_on(f, F, plan, across=(1.0, 0.0, 0.0))
    # The join with the next bay: the fan's half of the transverse rib (the
    # lierne bay beyond draws the other half).
    _sweep_on(f, F, [(-hx + 2 * hx * k / 16, hz) for k in range(17)], profile=HALF_PROFILE, across=(0.0, 0.0, 1.0),
              side=-1.0, p_range=(0.15, 0.3))
    _sweep_on(f, F, [(-hx + 2 * hx * k / 16, -hz) for k in range(17)], profile=HALF_PROFILE, across=(0.0, 0.0, 1.0),
              side=1.0, p_range=(0.15, 0.3))
    boss_at(f, (0.0, rise, 0.0), (0.0, 1.0, 0.0), 1.1, big=True)
    # Pendant drops under the two hanging corners.
    for cx, cz, base in F.corners:
        if base <= 0:
            continue
        _pendant(f, (cx, base, cz))
    p.meta.update(rise=rise, pendant=pendant, collider=_fan_collider(F, L))
    p.meta["surface"] = ("fan", w, h, rise)
    return p


def _quarter_dir(cx, cz, th):
    """Unit plan direction at angle th (0..pi/2) of the quarter of a corner
    conoid that faces the vault's middle."""
    sx = 1.0 if cx < 0 else -1.0
    sz = 1.0 if cz < 0 else -1.0
    return (sx * math.cos(th), sz * math.sin(th))


def _panel_outline(hx, hz, R, n):
    """The flat panel: the rectangle minus four quarter discs of radius R
    at its corners, counter-clockwise seen from below... any winding (the
    face is oriented by its normal hint)."""
    pts = []
    corners = [(-hx, -hz), (hx, -hz), (hx, hz), (-hx, hz)]
    for ci, (cx, cz) in enumerate(corners):
        # From the arc's end on the z edge to its end on the x edge (or reverse), walking round the panel.
        sx = 1.0 if cx < 0 else -1.0
        sz = 1.0 if cz < 0 else -1.0
        arc = [(cx + sx * R * math.cos(t * math.pi / 2 / n), cz + sz * R * math.sin(t * math.pi / 2 / n)) for t in range(n + 1)]
        # Walk: corner 0 (NW) from its x-edge point to its z-edge point? Order so the outline goes round.
        if ci in (0, 2):
            arc = list(reversed(arc))
        pts.extend(arc)
    out = []
    for q in pts:
        if not out or math.hypot(q[0] - out[-1][0], q[1] - out[-1][1]) > 1e-6:
            out.append(q)
    if math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) < 1e-6:
        out.pop()
    return out


CURTAIN_UP = 0.3  # a curtain runs on this far behind the web above it (no slit where tessellations differ)
CURTAIN_DOWN = 0.05  # and this far below the edge it hangs from (behind that edge's rib)


def _curtain(faces, plan, low, high, normal) -> None:
    """Vertical webs closing the join between two height fields along a
    plan line: `low` the fan's edge, `high` the lierne web beyond it
    (`normal` points from the fan to the lierne). Where the lierne web is
    higher, a web facing `normal` from just under the fan's edge to above the
    lierne web (seen from the lierne side); where it is lower, one facing
    back from under the lierne web to above the fan's edge (seen from the
    fan's side). The margins run behind the webs, so no slit opens where
    the tessellations differ."""
    def piece(x0, z0, f0, l0, x1, z1, f1, l1):
        if (l0 - f0) + (l1 - f1) >= 0:
            pts = [(x0, f0 - CURTAIN_DOWN, z0), (x1, f1 - CURTAIN_DOWN, z1), (x1, l1 + CURTAIN_UP, z1), (x0, l0 + CURTAIN_UP, z0)]
            add_face(faces, pts, WEB, None, normal)
        else:
            pts = [(x0, l0 - CURTAIN_UP, z0), (x1, l1 - CURTAIN_UP, z1), (x1, f1 + CURTAIN_DOWN, z1), (x0, f0 + CURTAIN_DOWN, z0)]
            add_face(faces, pts, WEB, None, (-normal[0], -normal[1], -normal[2]))

    for (xa, za), (xb, zb) in zip(plan, plan[1:]):
        fa, fb = low(xa, za), low(xb, zb)
        la, lb = high(xa, za), high(xb, zb)
        da, db = la - fa, lb - fb
        if da * db < 0:
            t = da / (da - db)
            xm, zm = xa + (xb - xa) * t, za + (zb - za) * t
            ym = fa + (fb - fa) * t
            piece(xa, za, fa, la, xm, zm, ym, ym)
            piece(xm, zm, ym, ym, xb, zb, fb, lb)
        else:
            piece(xa, za, fa, la, xb, zb, fb, lb)


def _pendant(faces, tip) -> None:
    """A drop under a pendant's tip: an octagonal moulded cone."""
    x, y, z = tip
    tiers = [(0.15, 0.32), (-0.15, 0.32), (-0.3, 0.24), (-0.55, 0.18), (-0.9, 0.0)]
    n = 8
    for k, ((b0, r0), (b1, r1)) in enumerate(zip(tiers, tiers[1:])):
        for i in range(n):
            a0, a1 = math.pi / n + 2 * math.pi * i / n, math.pi / n + 2 * math.pi * (i + 1) / n
            quad = [(x + r0 * math.cos(a0), y + b0, z + r0 * math.sin(a0)), (x + r0 * math.cos(a1), y + b0, z + r0 * math.sin(a1)),
                    (x + r1 * math.cos(a1), y + b1, z + r1 * math.sin(a1)), (x + r1 * math.cos(a0), y + b1, z + r1 * math.sin(a0))]
            mid = ((math.cos(a0) + math.cos(a1)) / 2, -0.3, (math.sin(a0) + math.sin(a1)) / 2)
            v0, v1 = 1.0 - k / (len(tiers) - 1), 1.0 - (k + 1) / (len(tiers) - 1)
            add_face(faces, quad, BOSS, [(i / n, v0), ((i + 1) / n, v0), ((i + 1) / n, v1), (i / n, v1)], mid)


def _fan_collider(F: FanSurface, L: LierneSurface):
    """A coarse shell of the fan: conoids 3 x 3, the panel, the curtains,
    a box round each pendant drop."""
    out = []
    R = F.r
    for cx, cz, base in F.corners:
        for i in range(3):
            for k in range(3):
                pts = []
                for a_i, r_k in ((i, k), (i + 1, k), (i + 1, k + 1), (i, k + 1)):
                    th = (a_i / 3) * math.pi / 2
                    r = R * r_k / 3
                    dx, dz = _quarter_dir(cx, cz, th)
                    pts.append((cx + r * dx, F.profile(base, r / R), cz + r * dz))
                for tri in ((pts[0], pts[1], pts[2]), (pts[0], pts[2], pts[3])):
                    if geom.norm(geom.newell(list(tri))) > 1e-9:
                        out.append(list(tri))
    panel = [(x, F.rise, z) for x, z in _panel_outline(F.hx, F.hz, R, 2)]
    for tri in geom.ear_clip(panel):
        out.append([panel[i] for i in tri])
    curt: list = []
    for sx in (-1.0, 1.0):
        _curtain(curt, [(sx * F.hx, -F.hz + 2 * F.hz * k / 4) for k in range(5)], F.height, L.height, (sx, 0.0, 0.0))
    _curtain(curt, [(-F.hx + 2 * F.hx * k / 4, F.hz) for k in range(5)], F.height, L.height, (0.0, 0.0, 1.0))
    out.extend(fc.pts for fc in curt)
    for cx, cz, base in F.corners:
        if base > 0:
            lo, hi = (cx - 0.35, base - 0.95, cz - 0.35), (cx + 0.35, base + 0.1, cz + 0.35)
            box: list = []
            geom.box(box, lo, hi, WEB, skip=("+y",))
            out.extend(fc.pts for fc in box)
    return out


def surface(meta_surface):
    """The height field a vault piece's meta["surface"] names (local frame)."""
    kind, w, h, rise = meta_surface
    return LierneSurface(w, h, rise) if kind == "lierne" else FanSurface(w, h, rise)
