"""Mouldings and stonework: plinth, string course and cornice sweeps, the
ceiling rib, quoins, bay shafts, corbels and the floor border (sizes in
castle_kit/grid.py). Frames as in geom.py: x along the
wall, y up from the floor, z out of the wall face."""

from __future__ import annotations

from castle_kit import grid as G
from castle_kit.pieces import geom
from castle_kit.pieces.geom import Piece, add_face

WALL = "castle_wall|world"
FLOOR = "castle_floor|world"


def trim(strip: str) -> str:
    return f"castle_trim|trim:{strip}"


def fit(tile: str) -> str:
    return f"castle_trim|fit:{tile}"


# Profiles [(z, y), ...] from the bottom at the wall, out and up, back to the
# wall at the top (geom.sweep). Depths and heights follow grid.PLINTH,
# STRING and CORNICE; no two profiles share a depth with the shafts' faces
# (0.25, 0.32) so nothing sits coplanar where they meet.
PLINTH_PROFILE = [(0.0, 0.0), (0.12, 0.0), (0.12, 0.38), (0.095, 0.43), (0.095, 0.5), (0.0, 0.6)]
STRING_PROFILE = [(0.0, 0.0), (0.07, 0.02), (0.15, 0.11), (0.15, 0.21), (0.11, 0.27), (0.0, 0.3)]
CORNICE_PROFILE = [(0.0, -0.70), (0.055, -0.645), (0.055, -0.565), (0.1, -0.52), (0.16, -0.41), (0.215, -0.31),
                   (0.26, -0.265), (0.26, -0.205), (0.35, -0.14), (0.35, 0.0), (0.0, 0.0)]
RIB_PROFILE = [(-0.15, 0.0), (-0.15, -0.05), (-0.12, -0.08), (-0.12, -0.12), (-0.07, -0.2), (-0.03, -0.22),
               (0.03, -0.22), (0.07, -0.2), (0.12, -0.12), (0.12, -0.08), (0.15, -0.05), (0.15, 0.0)]

assert abs(PLINTH_PROFILE[-1][1] - G.PLINTH.height) < 1e-9 and abs(max(z for z, _ in PLINTH_PROFILE) - G.PLINTH.proud) < 1e-9
assert abs(STRING_PROFILE[-1][1] - G.STRING.height) < 1e-9 and abs(max(z for z, _ in STRING_PROFILE) - G.STRING.proud) < 1e-9
assert abs(-CORNICE_PROFILE[0][1] - G.CORNICE.height) < 1e-9 and abs(max(z for z, _ in CORNICE_PROFILE) - G.CORNICE.proud) < 1e-9


def _sweep_piece(profile, strip: str, metres: float, length: float, ends, hide, y: float = 0.0) -> Piece:
    p = Piece("")
    prof = [(z, yy + y) for z, yy in profile]
    faces: list = []
    geom.sweep(faces, prof, 0.0, length, trim(strip), metres, ends=tuple(ends))
    # Drop hidden segments (the plinth's underside on the floor, the
    # cornice's top against the ceiling): sweep emits one face per segment
    # in profile order, then the caps.
    nseg = len(prof) - 1
    keep = [f for i, f in enumerate(faces) if not (i < nseg and i in hide)]
    p.faces = keep
    return p


def plinth(length: float, ends=("cap", "cap")) -> Piece:
    """`kit_stone_plinth`: 0.6 high, 0.12 proud, on the floor."""
    return _sweep_piece(PLINTH_PROFILE, "plinth", 0.6, length, ends, hide={0})


def string_course(length: float, ends=("cap", "cap")) -> Piece:
    """`kit_stone_string`: 0.3 high, 0.15 proud; y = 0 is its bottom (its
    top is the window sill)."""
    return _sweep_piece(STRING_PROFILE, "string", 0.3, length, ends, hide=set())


def cornice(length: float, ends=("miter", "miter")) -> Piece:
    """`kit_stone_cornice`: 0.7 high, 0.35 proud; y = 0 is its top, at the
    ceiling."""
    return _sweep_piece(CORNICE_PROFILE, "cornice", 0.7, length, ends, hide={len(CORNICE_PROFILE) - 2})


def rib(length: float, ends=("none", "none")) -> Piece:
    """`kit_stone_rib`: a ceiling rib 0.3 wide, 0.22 deep, along x; y = 0
    is the ceiling, z across the rib. Its ends die into the cornices or a
    boss ("none") or are cut square ("cap")."""
    p = Piece("")
    geom.sweep(p.faces, RIB_PROFILE, 0.0, length, trim("rib"), 0.3, ends=tuple(ends))
    return p


def quoin(y0: float, y1: float, corner: bool = False, proud: float = 0.1, side: int = -1, hand: int = 1) -> Piece:
    """`kit_stone_quoin`: dressed blocks per 0.6 m course from y0 to y1
    (frame y; courses keep the world's 0.6 m lines when the frame's origin
    is on one), alternately 0.8 and 0.5 m long, standing `proud` off the
    face. Jamb quoins (corner False) run from x = 0 toward `side`. Corner
    quoins wrap an outside corner of the wall mass at x = 0: the main face
    is z = 0 for x > 0, the return face x = 0 for z < 0 (facing -x); `hand`
    -1 mirrors them (the main face for x < 0)."""
    p = Piece("")
    y = y0
    k = 0
    while y < y1 - 1e-6:
        top = min(y1, (int(round(y / G.COURSE + 1e-6)) + 1) * G.COURSE)
        if top - y < 0.05:
            y = top
            continue
        length = 0.8 if k % 2 == 0 else 0.5
        if corner:
            ret = 0.5 if k % 2 == 0 else 0.8
            geom.box(p.faces, (-proud, y, 0.0), (length, top, proud), WALL, skip=("-z",))
            geom.box(p.faces, (-proud, y, -ret), (0.0, top, 0.0), WALL, skip=("+x", "+z"))
        else:
            x0, x1 = (-length, 0.0) if side < 0 else (0.0, length)
            geom.box(p.faces, (x0, y, 0.0), (x1, top, proud), WALL, skip=("-z",))
        y = top
        k += 1
    if corner and hand < 0:
        for f in p.faces:
            f.pts = [(-x, y, z) for x, y, z in reversed(f.pts)]
    return p


def _ring(faces, y0, y1, o0, o1, tag, metres, rotated=False):
    """Faces between two outlines [(x, z), ...] (from the wall on the left
    round the front to the wall on the right) at heights y0 and y1: the
    sides of a shaft, a wash or a bell, or (y0 == y1) a ledge."""
    s0 = geom.arc_lengths(o0)
    tot = s0[-1] or 1.0
    flat = abs(y1 - y0) < 1e-9
    grow = sum(abs(b[0]) + b[1] for b in o1) > sum(abs(a[0]) + a[1] for a in o0)
    for i in range(len(o0) - 1):
        a0, b0, a1, b1 = o0[i], o0[i + 1], o1[i], o1[i + 1]
        pts = [(a0[0], y0, a0[1]), (b0[0], y0, b0[1]), (b1[0], y1, b1[1]), (a1[0], y1, a1[1])]
        if rotated:
            uv = [(y0, s0[i] / tot * 0.7), (y0, s0[i + 1] / tot * 0.7), (y1, s0[i + 1] / tot * 0.7), (y1, s0[i] / tot * 0.7)]
        else:
            uv = [(s0[i], 0.0), (s0[i + 1], 0.0), (s0[i + 1], metres), (s0[i], metres)]
        if flat:
            n = (0.0, -1.0 if grow else 1.0, 0.0)
        else:
            mx, mz = (a0[0] + b0[0] + a1[0] + b1[0]) / 4, (a0[1] + b0[1] + a1[1] + b1[1]) / 4
            nn = geom.newell(pts)
            n = nn if nn[0] * mx + nn[2] * mz >= 0 else (-nn[0], -nn[1], -nn[2])
        add_face(faces, pts, tag, uv, n)


def _rect(half, depth):
    return [(-half, 0.0), (-half, depth), (half, depth), (half, 0.0)]


def _chamfered(half, depth, c):
    return [(-half, 0.0), (-half, depth - c), (-half + c, depth), (half - c, depth), (half, depth - c), (half, 0.0)]


def _rect6(half, depth):
    """A rectangle with the chamfered outline's point count (for rings between them)."""
    return [(-half, 0.0), (-half, depth), (-half, depth), (half, depth), (half, depth), (half, 0.0)]


def _top(faces, outline, y, tag, down=False):
    pts = [(x, y, z) for x, z in outline]
    dmax = max(z for _, z in outline) or 1.0
    add_face(faces, pts, tag, [(x, z / dmax * 0.3) for x, z in outline], (0.0, -1.0 if down else 1.0, 0.0), anchor=True)


def cluster_outline(w: float = G.SHAFT.height / 2.0, d: float = G.SHAFT.proud, steps: int = 8):
    """Plan outline [(x, z), ...] of a clustered shaft `2w` wide, `d` proud:
    a big roll between two small ones, fillets back to the wall; from the
    wall on the left round the front to the wall on the right."""
    import math

    big = w * 0.4
    small = (w - big) / 2.0
    zc = d - big
    pts = [(-w, 0.0), (-w, zc * 0.6)]
    cxs = -w + small
    for i in range(steps // 2 + 1):
        a = math.pi - math.pi * i / (steps // 2)
        pts.append((cxs + small * math.cos(a), zc * 0.6 + small * math.sin(a)))
    for i in range(steps + 1):
        a = math.pi - math.pi * i / steps
        pts.append((big * math.cos(a), zc + big * math.sin(a)))
    for i in range(steps // 2 + 1):
        a = math.pi - math.pi * i / (steps // 2)
        pts.append((-cxs + small * math.cos(a), zc * 0.6 + small * math.sin(a)))
    pts += [(w, zc * 0.6), (w, 0.0)]
    out = []
    for q in pts:
        if not out or abs(q[0] - out[-1][0]) + abs(q[1] - out[-1][1]) > 1e-7:
            out.append(q)
    return out


def _scaled(outline, kx: float, kz: float, dz: float = 0.0):
    return [(x * kx, z * kz + (dz if z > 1e-9 else 0.0)) for x, z in outline]


def _squared(outline, half: float, depth: float):
    """The outline pushed out to a rectangle `half` x `depth` (same point
    count, so rings between the two stay quads)."""
    out = []
    for x, z in outline:
        if z <= 1e-9:
            out.append((half if x > 0 else -half, 0.0))
        else:
            out.append((max(-half, min(half, x * half / max(1e-6, max(abs(q) for q, _ in outline)))), depth))
    return out


def shaft(height: float, top_clear: float = G.CORNICE.height) -> Piece:
    """`kit_stone_shaft_<h>`: a clustered bay shaft 0.7 wide, 0.25 proud,
    from the floor to the cornice (`height` minus `top_clear`): a base
    block with a wash, three rolls, a necking band, a bell capital and an
    abacus. Below 2.4 m nothing stands prouder than 0.25 (the intrusion
    rule): the base widens, it does not deepen. x = 0 is the bay
    boundary."""
    p = Piece("")
    w, d = G.SHAFT.height / 2.0, G.SHAFT.proud
    top = height - top_clear
    band = trim("capital_band")
    f = p.faces
    body = cluster_outline(w, d)
    # A chamfered base block, a roll on it, then the wash up to the shaft.
    base = _chamfered(w + 0.08, d, 0.08)
    _ring(f, 0.0, 0.6, base, base, band, 0.3)
    _ring(f, 0.6, 0.6, base, _chamfered(w + 0.05, d, 0.06), band, 0.3)
    _ring(f, 0.6, 0.66, _chamfered(w + 0.05, d, 0.06), _chamfered(w + 0.07, d, 0.07), band, 0.3)
    _ring(f, 0.66, 0.72, _chamfered(w + 0.07, d, 0.07), _chamfered(w + 0.03, d, 0.05), band, 0.3)
    _top(f, _chamfered(w + 0.03, d, 0.05), 0.72, band)
    wash = _squared(body, w + 0.02, d)
    _ring(f, 0.72, 0.85, wash, body, band, 0.3)
    neck0 = top - 0.62
    _ring(f, 0.85, neck0, body, body, trim("shaft"), 0.7, rotated=True)
    neck = _scaled(body, 1.06, 1.0, 0.03)
    _ring(f, neck0, neck0, body, neck, band, 0.3)
    _ring(f, neck0, neck0 + 0.1, neck, neck, band, 0.3)
    _ring(f, neck0 + 0.1, neck0 + 0.1, neck, body, band, 0.3)
    bell0, bell1 = neck0 + 0.1, top - 0.14
    flare = _squared(body, w + 0.1, d + 0.06)
    mid = [((a[0] + b[0]) / 2 * 0.98, (a[1] + b[1]) / 2) for a, b in zip(body, flare)]
    _ring(f, bell0, (bell0 + bell1) / 2, body, mid, band, 0.3)
    _ring(f, (bell0 + bell1) / 2, bell1, mid, flare, band, 0.3)
    abacus = _squared(body, w + 0.12, d + 0.07)
    _ring(f, bell1, bell1, flare, abacus, band, 0.3)
    _ring(f, bell1, top, abacus, abacus, band, 0.3)
    _top(f, _rect(w + 0.12, d + 0.07), top, band)
    p.meta.update(base_half=w + 0.08, base_proud=d, base_top=0.6, top=top)
    return p


def plain_outline(w: float = G.SHAFT.height / 2.0, d: float = G.SHAFT.proud, steps: int = 6):
    """Plan outline of the plain shaft: one roll between two fillets, from
    the wall on the left round the front to the wall on the right."""
    import math

    fillet = 0.06
    r = d - fillet
    pts = [(-w, 0.0), (-w, fillet)]
    for i in range(steps + 1):
        a = math.pi - math.pi * i / steps
        pts.append((r * math.cos(a), fillet + r * math.sin(a)))
    pts += [(w, fillet), (w, 0.0)]
    return pts


def shaft_plain(height: float, top_clear: float = G.CORNICE.height) -> Piece:
    """`kit_stone_shaft_<h>_plain` (castle kit M2): the repeated bay shaft,
    0.7 wide and 0.25 proud like the clustered one, at most
    grid.SHAFT_TRIS["plain"] triangles: a square base block, a roll and a
    wash, one roll with fillets up the shaft, a necking band, a bell and an
    abacus under the cornice (`height` minus `top_clear`). Below 2.4 m
    nothing stands prouder than 0.25 (the base widens, it does not deepen).
    x = 0 is the bay boundary."""
    p = Piece("")
    w, d = G.SHAFT.height / 2.0, G.SHAFT.proud
    top = height - top_clear
    band = trim("capital_band")
    f = p.faces
    body = plain_outline(w, d)
    base = _rect(w + 0.08, d)
    _ring(f, 0.0, 0.55, base, base, band, 0.3)
    _top(f, base, 0.55, band)
    # A roll on the base block, then the wash up to the shaft.
    roll0, roll1 = _squared(body, w + 0.05, d), _squared(body, w + 0.065, d)
    _ring(f, 0.55, 0.64, roll0, roll1, band, 0.3)
    _ring(f, 0.64, 0.78, roll1, body, band, 0.3)
    neck0 = top - 0.62
    _ring(f, 0.78, neck0, body, body, trim("shaft"), 0.7, rotated=True)
    neck = _scaled(body, 1.07, 1.0, 0.03)
    _ring(f, neck0, neck0, body, neck, band, 0.3)
    _ring(f, neck0, neck0 + 0.12, neck, neck, band, 0.3)
    bell1 = top - 0.14
    flare = _squared(body, w + 0.1, d + 0.06)
    _ring(f, neck0 + 0.12, bell1, neck, flare, band, 0.3)
    abacus = _rect(w + 0.12, d + 0.07)
    _ring(f, bell1, top, abacus, abacus, band, 0.3)
    _top(f, abacus, bell1, band, down=True)
    _top(f, abacus, top, band)
    p.meta.update(base_half=w + 0.08, base_proud=d, base_top=0.55, top=top, variant="plain")
    return p


def _oct_ring(faces, y0, r0, y1, r1, tag, b0, b1, n=8):
    """An octagonal frustum band about +Y with fit-tile UVs (a round, b down)."""
    import math

    pts0 = [(r0 * math.cos(math.pi / n + 2 * math.pi * i / n), r0 * math.sin(math.pi / n + 2 * math.pi * i / n)) for i in range(n)]
    pts1 = [(r1 * math.cos(math.pi / n + 2 * math.pi * i / n), r1 * math.sin(math.pi / n + 2 * math.pi * i / n)) for i in range(n)]
    for i in range(n):
        j = (i + 1) % n
        quad = [(pts0[i][0], y0, pts0[i][1]), (pts0[j][0], y0, pts0[j][1]), (pts1[j][0], y1, pts1[j][1]),
                (pts1[i][0], y1, pts1[i][1])]
        mx, mz = (pts0[i][0] + pts0[j][0]) / 2, (pts0[i][1] + pts0[j][1]) / 2
        nn = geom.newell(quad)
        nrm = nn if nn[0] * mx + nn[2] * mz >= 0 else (-nn[0], -nn[1], -nn[2])
        add_face(faces, quad, tag, [(i / n, b0), ((i + 1) / n, b0), ((i + 1) / n, b1), (i / n, b1)], nrm)


def boss(size: float = 0.9) -> Piece:
    """`kit_stone_boss`: a carved octagonal boss where ribs cross; y = 0 is
    the ceiling, the boss hangs below the ribs (0.22 m) to 0.42 m."""
    p = Piece("")
    r = size / 2
    tag = fit("boss")
    tiers = [(0.0, r), (-0.07, r), (-0.12, r * 0.86), (-0.24, r * 0.6), (-0.3, r * 0.38), (-0.38, r * 0.2), (-0.42, 0.0)]
    for k, ((y0, r0), (y1, r1)) in enumerate(zip(tiers, tiers[1:])):
        _oct_ring(p.faces, y0, r0, y1, r1, tag, 1.0 - k / (len(tiers) - 1), 1.0 - (k + 1) / (len(tiers) - 1))
    return p


def corbel(size: float) -> Piece:
    """`kit_stone_corbel_<s>`: a moulded bracket projecting `size` from the
    wall and `size` high, 0.7 x size wide (at least 0.5 m); its top at
    y = 0 (under a cornice or a rib's springing)."""
    p = Piece("")
    hw = max(0.25, size * 0.35)
    s_ = size
    prof = [(0.0, -s_), (0.45 * s_, -0.8 * s_), (0.45 * s_, -0.7 * s_), (0.75 * s_, -0.55 * s_), (0.75 * s_, -0.4 * s_),
            (s_, -0.25 * s_), (s_, 0.0), (0.0, 0.0)]
    lens = geom.profile_lengths(prof[:-1])
    total = lens[-1]
    for i in range(len(prof) - 2):
        (za, ya), (zb, yb) = prof[i], prof[i + 1]
        pts = [(-hw, ya, za), (hw, ya, za), (hw, yb, zb), (-hw, yb, zb)]
        uv = [(0.0, lens[i] / total), (1.0, lens[i] / total), (1.0, lens[i + 1] / total), (0.0, lens[i + 1] / total)]
        add_face(p.faces, pts, fit("corbel_front"), uv, (0.0, -(zb - za), yb - ya))
    # The top shows from above only, between the corbel and the cornice.
    add_face(p.faces, [(-hw, 0.0, 0.0), (-hw, 0.0, s_), (hw, 0.0, s_), (hw, 0.0, 0.0)], fit("corbel_front"),
             [(0, 1), (0, 1), (1, 1), (1, 1)], (0.0, 1.0, 0.0))
    for sx in (-1.0, 1.0):
        pts = [(sx * hw, y, z) for z, y in prof]
        add_face(p.faces, pts, fit("corbel_side"), [(z / s_, 1.0 + y / s_) for z, y in prof], (sx, 0.0, 0.0))
    return p


def floor_border(rects, y: float = 0.0) -> Piece:
    """`kit_floor_border`: flush slab bands (world rectangles [x0, z0, x1, z1]
    at height y) that replace the floor's edge; built in world space."""
    p = Piece("")
    for x0, z0, x1, z1 in rects:
        along_x = (x1 - x0) >= (z1 - z0)
        pts = [(x0, y, z0), (x1, y, z0), (x1, y, z1), (x0, y, z1)]
        if along_x:
            uv = [(x, (z - z0) / max(1e-6, z1 - z0) * 0.3) for x, _, z in pts]
        else:
            uv = [(z, (x - x0) / max(1e-6, x1 - x0) * 0.3) for x, _, z in pts]
        add_face(p.faces, pts, trim("string"), uv, (0, 1, 0))
    return p
