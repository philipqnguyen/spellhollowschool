"""The talking portraits' frame: a gilt shadow box (NPCs, docs/art/castle-kit.md
"Portraits and tapestries"), pure Python (no `bpy`).

Frame: x along the wall (to the right seen from the room), y up, z out of
the wall face; the origin is the canvas centre on the wall face (where
sim_core::npcs::PortraitsFile::resolve puts `pos`), so a placement is the
origin at `pos` turned by `yaw`. The canvas opening is the placement's
`size` [w, h]: the picture plane at z = OPEN_Z, the box's back DEPTH (0.35
m) behind it at BACK_Z, just off the wall. The client stands the painted
subject inside the box (0.15 m off the back), so it reads as a figure in
a little room behind the glass-less opening, not a flat canvas: with the
subject moving (breathing, blinking, the head following you), that is
what tells a talking portrait from the decorative clusters' flat ones.

Two nodes at LOD0, one at LOD1:

- `castle_trim` (the castle's trim sheet, bound by name like the landing
  rail's): the moulding round the opening (sight-edge lip, bead, crest,
  outer flat, the box's outer sides down to the wall, dark oak behind a gilt edge), a raised boss on
  each corner, a ribbed shell crest on the top rail and a blank plaque on
  the bottom rail, all on the sheet's `frame_gilt` tile (the decorative
  frames' gilt) but the plaque (`plaque`);
- `npc_backdrop`: the box's back and four inner sides, UVs into one
  painted backdrop (`backdrop_<n>.png`, backdrops.py): the opening is the
  texture's inner square (inside the painted ring), the back a smaller
  square seen through it from EYE_M in front, the sides the bands between,
  so the painting runs on round the box like a diorama;
- LOD1 (`npc_backdrop_lod1`, 10 triangles): one box over the frame's outer
  rectangle, its front the whole backdrop texture (the painted gilt ring
  standing in for the moulding), its sides the ring's outer edge.

Triangles: about 130 at LOD0 (frame ~120, backdrop 10), 10 at LOD1;
budget 400 / 12 (docs/architecture.md "NPCs", Art). COLOR_0 is the castle
shader's painted value (1 lit front, darker sides and lip; the box inside
darkens toward the back)."""

from __future__ import annotations

import math

from castle_kit.pieces import geom
from castle_kit.pieces.geom import add_face

DEPTH = 0.35  # the canvas opening to the box's back (the contract's recess)
BACK_Z = 0.012  # the box's back stands off the wall face (never on its plane)
OPEN_Z = BACK_Z + DEPTH  # the picture plane: the opening's sight edge
BORDER = 0.13  # the moulding's width seen from the front
SUBJECT_Z = BACK_Z + 0.15  # where the client stands the subject (documented, the manifest's subject_z)
EYE_M = 1.2  # the backdrop's back square is the back seen through the opening from this far in front
RING_PX = 40  # backdrops.py: the painted gilt ring (LOD1's moulding), px of 512
RING_GAP_PX = 6  # LOD0's UVs stay this far inside the ring (no mip bleed)
TEX_PX = 512
LOD1_M = 18.0  # the LOD1 box from here on (beyond look_radius_m and alive_radius_m)
TRIS_LOD0 = 400
TRIS_LOD1 = 12
GILT = "castle_trim|fit:frame_gilt"
OAK = "castle_trim|fit:frame_oak"
GILT_EDGE = 0.04  # the outer side is gilt this far back from the outer flat, dark oak behind (the box)
PLAQUE = "castle_trim|fit:plaque"

# The moulding's profile from the opening outward: (d metres out from the
# sight edge, z above OPEN_Z, b on the frame_gilt tile: 1 at the sight
# edge, 0 at the outer edge; trim.py paints bead, hollow, crest, edge).
PROFILE = (
    (0.0, 0.0, 1.0),  # the lip's foot (the box's inner side meets it)
    (0.0, 0.035, 0.9),  # the sight edge
    (0.035, 0.05, 0.76),  # the bead
    (0.07, 0.085, 0.5),  # the crest
    (BORDER, 0.05, 0.12),  # the outer flat's edge
)
OUTER_B = 0.0  # the outer side (frame edge to the wall) samples the tile's dark outer band
# Shade (COLOR_0) per strip, inner to outer, then the outer side.
STRIP_SHADE = (0.72, 0.96, 1.0, 0.92)
SIDE_SHADE = 0.74
BOSS = 0.12  # the corner bosses' side
BOSS_Z = 0.115  # their top above OPEN_Z
CREST_W = 0.46  # the shell crest on the top rail: width, height above the frame, lobes
CREST_H = 0.15
CREST_LOBES = 8
CREST_Z = (0.03, 0.075, -0.06)  # its front's valley and ridge z, its back's (above OPEN_Z)
PLAQUE_WH = (0.26, 0.075)
PLAQUE_Z = 0.07  # its face above OPEN_Z
PLAQUE_T = 0.012


def tex_inner() -> tuple[float, float]:
    """(lo, hi) of the backdrop texture's painting inside the ring and its
    gap, in UV units (both axes)."""
    lo = (RING_PX + RING_GAP_PX) / TEX_PX
    return lo, 1.0 - lo


def back_scale() -> float:
    """The back's size seen through the opening from EYE_M, as a fraction of the opening."""
    return EYE_M / (EYE_M + DEPTH)


def outer_extent(size) -> tuple[float, float, float, float]:
    """(x0, x1, y0, y1) of the whole frame round the canvas centre (the
    moulding, the crest above, the bosses): what the decorative clusters
    keep clear of (placements.keep_outs)."""
    w, h = size
    hx = w / 2 + BORDER + max(0.0, BOSS / 2 - BORDER / 2)
    top = h / 2 + BORDER + CREST_H
    bot = h / 2 + BORDER + max(0.0, BOSS / 2 - BORDER / 2)
    return (-hx, hx, -bot, top)


def frame_depth() -> float:
    """How far the frame stands off the wall at its deepest (the bosses)."""
    return OPEN_Z + BOSS_Z


# -- the moulding ------------------------------------------------------------------------


def _ring(w: float, h: float, d: float) -> list[tuple[float, float]]:
    """The opening's rectangle grown by d, counter-clockwise from the bottom
    left (seen from the front)."""
    return [(-w / 2 - d, -h / 2 - d), (w / 2 + d, -h / 2 - d), (w / 2 + d, h / 2 + d), (-w / 2 - d, h / 2 + d)]


def _u(side: int, pt, w: float, h: float) -> float:
    """0..1 along a side (bottom, right, top, left: as the ring runs) by the
    point's coordinate across the frame's outer span."""
    hx, hy = w / 2 + BORDER, h / 2 + BORDER
    if side in (0, 2):
        return (pt[0] + hx) / (2 * hx)
    return (pt[1] + hy) / (2 * hy)


def moulding(faces: list, shades: list, w: float, h: float) -> None:
    """The profile swept round the opening (mitred at the corners), and the
    box's outer sides from the outer edge back to the wall."""
    rings = [(_ring(w, h, d), OPEN_Z + z, b) for d, z, b in PROFILE]
    for k in range(len(rings) - 1):
        (ra, za, ba), (rb, zb, bb) = rings[k], rings[k + 1]
        for i in range(4):
            j = (i + 1) % 4
            pts = [(ra[i][0], ra[i][1], za), (ra[j][0], ra[j][1], za), (rb[j][0], rb[j][1], zb), (rb[i][0], rb[i][1], zb)]
            uv = [(_u(i, ra[i], w, h), ba), (_u(i, ra[j], w, h), ba), (_u(i, rb[j], w, h), bb), (_u(i, rb[i], w, h), bb)]
            n = len(faces)
            add_face(faces, pts, GILT, uv, _out_normal(i, k == 0))
            shades.extend([STRIP_SHADE[k]] * (len(faces) - n))
    ro, zo, bo = rings[-1]
    zg = zo - GILT_EDGE
    for i in range(4):
        j = (i + 1) % 4
        ua, ub = _u(i, ro[i], w, h), _u(i, ro[j], w, h)
        pts = [(ro[i][0], ro[i][1], zg), (ro[j][0], ro[j][1], zg), (ro[j][0], ro[j][1], zo), (ro[i][0], ro[i][1], zo)]
        n = len(faces)
        add_face(faces, pts, GILT, [(ua, OUTER_B), (ub, OUTER_B), (ub, bo), (ua, bo)], _side_normal(i))
        shades.extend([SIDE_SHADE] * (len(faces) - n))
        # The box behind the moulding: dark oak back to the wall.
        pts = [(ro[i][0], ro[i][1], 0.0), (ro[j][0], ro[j][1], 0.0), (ro[j][0], ro[j][1], zg), (ro[i][0], ro[i][1], zg)]
        n = len(faces)
        add_face(faces, pts, OAK, [(ua, 0.2), (ub, 0.2), (ub, 0.8), (ua, 0.8)], _side_normal(i))
        shades.extend([0.62] * (len(faces) - n))


def _side_normal(i: int) -> tuple[float, float, float]:
    return ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0))[i]


def _out_normal(i: int, lip: bool) -> tuple[float, float, float]:
    """The lip faces into the opening; every other strip faces the room (its
    front), so add_face's winding follows +z."""
    if lip:
        nx, ny, _ = _side_normal(i)
        return (-nx, -ny, 0.0)
    return (0.0, 0.0, 1.0)


def bosses(faces: list, shades: list, w: float, h: float) -> None:
    """A raised square boss on each corner of the moulding: a low pyramid
    on four sides."""
    s = BOSS / 2
    z0, z1 = OPEN_Z + PROFILE[-1][1], OPEN_Z + BOSS_Z
    zm = z1 - 0.03
    for cx, cy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x, y = cx * (w / 2 + BORDER / 2), cy * (h / 2 + BORDER / 2)
        q = [(x - s, y - s), (x + s, y - s), (x + s, y + s), (x - s, y + s)]
        top = (x, y, z1)
        for i in range(4):
            a, b = q[i], q[(i + 1) % 4]
            n = len(faces)
            add_face(faces, [(a[0], a[1], zm), (b[0], b[1], zm), top], GILT, [(0.0, 0.62), (1.0, 0.62), (0.5, 0.45)],
                     (0.0, 0.0, 1.0))
            nrm = _side_normal(i)
            add_face(faces, [(a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], zm), (a[0], a[1], zm)], GILT,
                     [(0.0, 0.88), (1.0, 0.88), (1.0, 0.75), (0.0, 0.75)], nrm)
            shades.extend([1.0 if i in (2, 3) else 0.86] * (len(faces) - n - 1) + [0.8])


def crest(faces: list, shades: list, w: float, h: float) -> None:
    """A ribbed shell on the top rail, its base on the rail's outer flat: a
    fan of lobes whose ridges stand proud of the valleys, with a rim back
    toward the wall."""
    zv, zr, zb = (OPEN_Z + z for z in CREST_Z)
    y0 = h / 2 + BORDER * 0.45
    hw = CREST_W / 2
    centre = (0.0, y0, zr + 0.02)
    arc = []
    for i in range(CREST_LOBES + 1):
        a = math.pi * i / CREST_LOBES
        x, y = -hw * math.cos(a), y0 + (CREST_H + BORDER * 0.55) * math.sin(a)
        arc.append((x, y, zr if i % 2 else zv))
    for i in range(CREST_LOBES):
        a, b = arc[i], arc[i + 1]
        n = len(faces)
        add_face(faces, [centre, a, b], GILT, [(0.5, 0.5), (i / CREST_LOBES, 0.05), ((i + 1) / CREST_LOBES, 0.05)],
                 (0.0, 0.0, 1.0))
        out = (a[0] + b[0], a[1] + b[1] - 2 * y0, 0.0)
        add_face(faces, [(a[0], a[1], zb), (b[0], b[1], zb), b, a], GILT,
                 [(i / CREST_LOBES, 0.0), ((i + 1) / CREST_LOBES, 0.0), ((i + 1) / CREST_LOBES, 0.12),
                  (i / CREST_LOBES, 0.12)], out)
        lit = 1.0 if arc[i][0] < 0 else 0.88
        shades.extend([lit] * (len(faces) - n - 1) + [0.78])


def plaque(faces: list, shades: list, w: float, h: float) -> None:
    """A blank plaque on the bottom rail (no text): its face and edges."""
    pw, ph = PLAQUE_WH
    y = -h / 2 - BORDER / 2
    z1, z0 = OPEN_Z + PLAQUE_Z, OPEN_Z + PLAQUE_Z - PLAQUE_T - 0.04
    q = [(-pw / 2, y - ph / 2), (pw / 2, y - ph / 2), (pw / 2, y + ph / 2), (-pw / 2, y + ph / 2)]
    n = len(faces)
    add_face(faces, [(p[0], p[1], z1) for p in q], PLAQUE, [(0, 0), (1, 0), (1, 1), (0, 1)], (0.0, 0.0, 1.0))
    shades.extend([1.0] * (len(faces) - n))
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        n = len(faces)
        add_face(faces, [(a[0], a[1], z0), (b[0], b[1], z0), (b[0], b[1], z1), (a[0], a[1], z1)], PLAQUE,
                 [(0, 0), (1, 0), (1, 0.08), (0, 0.08)], _side_normal(i))
        shades.extend([0.8] * (len(faces) - n))


def frame_piece(size) -> tuple[geom.Piece, list[float]]:
    """The gilt node's faces (castle_trim tags) and their shades, in face order."""
    w, h = size
    p = geom.Piece("")
    shades: list[float] = []
    moulding(p.faces, shades, w, h)
    bosses(p.faces, shades, w, h)
    crest(p.faces, shades, w, h)
    plaque(p.faces, shades, w, h)
    assert len(shades) == len(p.faces)
    p.walkthrough = True
    p.meta.update(size=(w, h), depth=DEPTH, border=BORDER)
    return p, shades


# -- the backdrop box and LOD1 -----------------------------------------------------------


def backdrop_faces(size) -> list[tuple[list, list, list]]:
    """The box's back and inner sides: [(pts, uvs, shades)], each facing
    into the box (toward the viewer)."""
    w, h = size
    lo, hi = tex_inner()
    s = back_scale()
    mid = (lo + hi) / 2
    half = (hi - lo) / 2

    def uv_open(x, y):
        return (mid + half * x / (w / 2), mid + half * y / (h / 2))

    def uv_back(x, y):
        return (mid + half * s * x / (w / 2), mid + half * s * y / (h / 2))

    o = _ring(w, h, 0.0)
    out = [([(x, y, BACK_Z) for x, y in o], [uv_back(x, y) for x, y in o], [0.82] * 4)]
    for i in range(4):
        a, b = o[i], o[(i + 1) % 4]
        pts = [(a[0], a[1], BACK_Z), (b[0], b[1], BACK_Z), (b[0], b[1], OPEN_Z), (a[0], a[1], OPEN_Z)]
        uv = [uv_back(*a), uv_back(*b), uv_open(*b), uv_open(*a)]
        sh = [0.58, 0.58, 0.86, 0.86] if i != 2 else [0.5, 0.5, 0.74, 0.74]  # the top side under the lintel darker
        out.append((pts, uv, sh))
    return out


def lod1_faces(size) -> list[tuple[list, list, list]]:
    """LOD1: the frame's outer box, its front the whole backdrop texture."""
    w, h = size
    zf = OPEN_Z + PROFILE[-1][1]
    o = _ring(w, h, BORDER)
    out = [([(x, y, zf) for x, y in o], [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], [1.0] * 4)]
    r = RING_PX / TEX_PX * 0.35  # the ring's outer, darker band
    side_uv = (((0, 0), (1, 0), (1, r), (0, r)), ((1, 0), (1, 1), (1 - r, 1), (1 - r, 0)),
               ((1, 1), (0, 1), (0, 1 - r), (1, 1 - r)), ((0, 1), (0, 0), (r, 0), (r, 1)))
    for i in range(4):
        a, b = o[i], o[(i + 1) % 4]
        pts = [(a[0], a[1], 0.0), (b[0], b[1], 0.0), (b[0], b[1], zf), (a[0], a[1], zf)]
        out.append((pts, [tuple(float(c) for c in q) for q in side_uv[i]], [SIDE_SHADE] * 4))
    return out


def triangles(faces) -> int:
    return sum(len(f[0]) - 2 for f in faces)


def lod0_triangles(size) -> int:
    p, _ = frame_piece(size)
    return p.triangles() + triangles(backdrop_faces(size))
