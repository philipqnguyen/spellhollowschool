"""Castle M5a corridor pieces (docs/art/castle-kit.md "Pieces"; the Corridors
builder): the Charms class door, the robed statues, the tapestries, the
Long Gallery's rose window and the Undercroft's dry well.

Frames as geom.py: x along the wall (to the right seen from the room), y
up, z out of the wall face into the room. Openings (the class door, the
rose window) put y = 0 at their sill like openings.py; heroes put their
origin where castle_kit.json's `pos` says (a statue at its niche's centre on
the sill, 0.35 m into the niche; a tapestry at its cloth's bottom centre on
the wall face; the well at its centre on the floor).

Everything is our own invented design: the statues are no real person
(two robed scholars, one hooded with a book, one with a
staff), the tapestries' designs are castle_kit/tapestries.py's, the class
door's plaque carries a plain star (no text, no crest). Colours: palette
names only (grid.palette_tag), the stone and wood of the trim and wood
sheets, the tapestry sheet; no colour literal here (PaletteRuleTests)."""

from __future__ import annotations

import math

import meshkit as mk
from castle_kit import grid as G
from castle_kit.pieces import geom, openings
from castle_kit.pieces.geom import Piece, add_face
from castle_kit.pieces.hall import from_mesh

WALL = openings.WALL
BRASS = G.palette_tag("metal", "brass")
GILT = G.palette_tag("metal", "gilt")
IRON = G.palette_tag("metal", "iron")


def _uv_square(side, pts):
    return [(0, 0), (1, 0), (1, 1), (0, 1)]


# -- the Charms class door (kit_door_class_3x4.5) ----------------------------------------------

PLAQUE_GAP = 0.15  # the plaque's bottom over the arch's apex (grid.CLASS_DOOR_PLAQUE)
PLAQUE_DEPTH = 0.04


def class_door(width: float, height: float, depth: float = 0.3) -> Piece:
    """`kit_door_class_<w>x<h>`: the blind door's closed oak pair in its
    pointed recess `depth` deep with its voussoir surround (openings.
    blind_door), and a brass plaque grid.CLASS_DOOR_PLAQUE over the
    surround's apex with a raised gilt star and a gilt edge (no text);
    y = 0 is the floor."""
    p = openings.blind_door(width, height, depth)
    f = p.faces
    spring = p.meta["spring"]
    # The surround's outer apex (openings._surround: band 0.3 round the head).
    outer = geom.pointed_arc(width, spring, openings.SEG, grow=0.3)
    apex = max(y for _, y in outer)
    pw, ph = G.CLASS_DOOR_PLAQUE
    y0 = apex + PLAQUE_GAP
    plaque_faces(f, y0)
    p.meta.update(plaque=(-pw / 2, pw / 2, y0, y0 + ph), apex=apex)
    return p


def plaque_faces(f: list, y0: float) -> None:
    """The class door's brass plaque (grid.CLASS_DOOR_PLAQUE) with its gilt
    edge and star, its bottom centre at (0, y0) on the wall face (z = 0)."""
    pw, ph = G.CLASS_DOOR_PLAQUE
    z0, z1 = 0.0, PLAQUE_DEPTH
    geom.box(f, (-pw / 2, y0, z0), (pw / 2, y0 + ph, z1), BRASS, skip=("-z",))
    # A gilt edge (a thin frame standing 1 cm proud) and the star in the middle.
    e = 0.035
    for x0, x1, ya, yb in ((-pw / 2, pw / 2, y0, y0 + e), (-pw / 2, pw / 2, y0 + ph - e, y0 + ph),
                           (-pw / 2, -pw / 2 + e, y0 + e, y0 + ph - e), (pw / 2 - e, pw / 2, y0 + e, y0 + ph - e)):
        geom.box(f, (x0, ya, z1), (x1, yb, z1 + 0.01), GILT, skip=("-z",))
    cy = y0 + ph / 2
    star = []
    for k in range(10):
        a = math.pi / 2 + k * math.pi / 5
        r = 0.12 if k % 2 == 0 else 0.05
        star.append((r * math.cos(a), cy + r * math.sin(a)))
    add_face(f, [(x, y, z1 + 0.012) for x, y in star], GILT, None, (0.0, 0.0, 1.0))
    for i in range(10):
        (xa, ya), (xb, yb) = star[i], star[(i + 1) % 10]
        add_face(f, [(xa, ya, z1), (xb, yb, z1), (xb, yb, z1 + 0.012), (xa, ya, z1 + 0.012)], GILT, None,
                 (yb - ya, -(xb - xa), 0.0))


# The plaque alone, marking a class door that is an open
# doorway (the Potions classroom's) or a blind door of the corridor kit.
CLASS_PLAQUE_Y = (1.8, 6.0)  # the plaque's bottom above the floor


def class_plaque(y: float = 2.0) -> Piece:
    """`kit_class_plaque`: the class door's brass plaque (grid.
    CLASS_DOOR_PLAQUE, a raised gilt star and edge, no text) on a wall face,
    its bottom `y` above the floor (CLASS_PLAQUE_Y). Origin at the floor on
    the wall face under the plaque's centre; it stands PLAQUE_DEPTH + 2 cm
    proud, all of it at least 1.8 m up, so nothing intrudes below it."""
    if not CLASS_PLAQUE_Y[0] - 1e-9 <= y <= CLASS_PLAQUE_Y[1] + 1e-9:
        raise ValueError(f"kit_class_plaque: y within {CLASS_PLAQUE_Y} m")
    p = Piece("")
    plaque_faces(p.faces, y)
    pw, ph = G.CLASS_DOOR_PLAQUE
    p.meta.update(plaque=(-pw / 2, pw / 2, y, y + ph))
    return p


# -- robed statues (kit_statue_robed_a, _b) -------------------------------------------------------
#
# Zones -> palette names: the figure in the pale statue stone, the plinth in the
# carved stone of the hall's beasts, a gilt book clasp or orb.
STATUE_ZONES = {1: "statue_stone", 2: "beast_stone", 3: "gilt"}
PLINTH_H = 0.42
SEGS = 14


def statue_robed(variant: str) -> Piece:
    """`kit_statue_robed_a` (hooded, an open book held before the chest) and
    `_b` (bareheaded and bearded, a tall staff with an orb in the right
    hand): an invented robed scholar about 2.4 m tall on a 0.42 m plinth.
    It stands in a kit_niche_2x3.5 (grid.STATUE_*): local z from -0.2 (0.15
    clear of the niche's back with the origin 0.35 m into it) to 0.58 (0.23
    proud of the wall face), x within +-0.85, the head and the staff's orb
    under the niche's pointed head with 0.15 m clear."""
    m = mk.Mesh()
    S, P, Gd = 1, 2, 3
    # The plinth: a block with a moulded cap and foot (three stacked boxes).
    mk.box(m, (-0.44, 0.0, -0.2), (0.44, 0.08, 0.38), "stone", P, skip=("-y",))
    mk.box(m, (-0.4, 0.08, -0.18), (0.4, PLINTH_H - 0.07, 0.34), "stone", P, skip=("-y",))
    mk.box(m, (-0.44, PLINTH_H - 0.07, -0.2), (0.44, PLINTH_H, 0.38), "stone", P, skip=("-y",))
    y = PLINTH_H
    cz = 0.08  # the figure's axis, a little forward of the plinth's middle
    # The robe: a flared hem, the body, the shoulders (an oval lathe).
    robe = [(0.34, 0.0), (0.33, 0.12), (0.29, 0.55), (0.25, 1.05), (0.235, 1.35), (0.25, 1.62), (0.27, 1.74),
            (0.2, 1.84), (0.085, 1.9)]
    mk.lathe(m, [(r, y + h) for r, h in robe], SEGS, "metal", S, (0.0, 0.0, cz), scale_xz=(1.0, 0.68),
             cap_top=True)
    # Folds down the front (three shallow ribs), a belt.
    for dx in (-0.12, 0.0, 0.12):
        mk.box(m, (dx - 0.025, y + 0.05, cz + 0.17), (dx + 0.025, y + 1.0, cz + 0.215 - abs(dx) * 0.2), "metal", S,
               skip=("-z", "-y"))
    mk.lathe(m, [(0.245, y + 1.12), (0.25, y + 1.18)], SEGS, "metal", S, (0.0, 0.0, cz), scale_xz=(1.0, 0.7),
             cap_top=False)
    # The head (and a hood or a beard).
    head_y = y + 1.98
    mk.lathe(m, [(0.0, -0.13), (0.08, -0.11), (0.12, -0.04), (0.12, 0.04), (0.085, 0.11), (0.0, 0.14)], 10, "metal", S,
             (0.0, head_y, cz + 0.02), scale_xz=(0.9, 1.0))
    if variant == "a":
        hood = [(0.2, -0.16), (0.19, 0.0), (0.16, 0.12), (0.09, 0.2), (0.0, 0.23)]
        mk.lathe(m, hood, 10, "metal", S, (0.0, head_y, cz - 0.03), scale_xz=(0.95, 0.8), cap_top=False)
    else:
        beard = [(-0.07, -0.06), (0.07, -0.06), (0.03, -0.3), (-0.03, -0.3)]
        mk.prism(m, [(x, head_y + yy) for x, yy in beard], cz + 0.06, cz + 0.13, "metal", S)
    # Sleeves and hands.
    if variant == "a":
        for sx in (-1.0, 1.0):
            mk.rod(m, (sx * 0.24, y + 1.68, cz), (sx * 0.15, y + 1.3, cz + 0.2), 0.075, 8, "metal", S)
            mk.lathe(m, [(0.075, 0.0), (0.095, 0.12)], 8, "metal", S, (sx * 0.13, y + 1.18, cz + 0.22), cap_top=True)
        # The open book: two leaves in a shallow V, a gilt clasp.
        for sx in (-1.0, 1.0):
            m.poly([(0.0, y + 1.24, cz + 0.32), (sx * 0.2, y + 1.27, cz + 0.29), (sx * 0.2, y + 1.48, cz + 0.33),
                    (0.0, y + 1.45, cz + 0.36)][:: int(sx)], "metal", zone=S)
        mk.box(m, (-0.21, y + 1.22, cz + 0.26), (0.21, y + 1.25, cz + 0.33), "metal", Gd)
    else:
        # Right arm forward to the staff, the left across the waist.
        mk.rod(m, (0.24, y + 1.68, cz), (0.3, y + 1.32, cz + 0.16), 0.075, 8, "metal", S)
        mk.rod(m, (-0.24, y + 1.68, cz), (-0.02, y + 1.2, cz + 0.2), 0.07, 8, "metal", S)
        mk.box(m, (0.27, y + 1.24, cz + 0.12), (0.37, y + 1.36, cz + 0.22), "metal", S)
        top = y + 2.48
        mk.rod(m, (0.32, PLINTH_H, cz + 0.17), (0.32, top, cz + 0.17), 0.028, 6, "metal", S)
        mk.lathe(m, [(0.0, -0.08), (0.06, -0.05), (0.075, 0.0), (0.06, 0.05), (0.0, 0.08)], 8, "metal", Gd,
                 (0.32, top + 0.08, cz + 0.17))
    p = from_mesh(m, STATUE_ZONES)
    lo, hi = m.bounds()
    p.meta.update(height=hi[1], depth=(lo[2], hi[2]), width=(lo[0], hi[0]))
    return p


# -- tapestries (kit_tapestry_<w>x<h>) -------------------------------------------------------------

TAPESTRY_FOLDS = 0.035  # the pleats' depth: the cloth stands 0.02..0.055 m off the wall
ROD_R = 0.03


def tapestry(width: float, height: float, tile: float = 0.0) -> Piece:
    """`kit_tapestry_<w>x<h>`: a cloth `width` x `height` showing tile
    `tapestry_<tile>` of the tapestries sheet (castle_tapestry, fit: the
    tile's aspect is the piece's), hung in shallow pleats from an iron rod
    with gilt finials on two brackets. Origin: the cloth's bottom centre on
    the wall face; at most grid.TAPESTRY_PROUD off the wall."""
    names = G.TAPESTRY_PIECES[G.piece_name("tapestry", None, width, height)]
    t = int(round(tile))
    if not 0 <= t < 4 or f"tapestry_{t}" not in names:
        raise ValueError(f"kit_tapestry_{G.fmt_m(width)}x{G.fmt_m(height)} shows tiles {names}, not tapestry_{t}")
    tag = f"castle_tapestry|fit:tapestry_{t}"
    p = Piece("")
    f = p.faces
    folds = max(4, int(round(width / 0.42)))
    xs = [-width / 2 + width * i / folds for i in range(folds + 1)]
    zs = [0.02 + (TAPESTRY_FOLDS if i % 2 else 0.0) for i in range(folds + 1)]
    rows = (0.0, height * 0.5, height)
    for j in range(len(rows) - 1):
        ya, yb = rows[j], rows[j + 1]
        for i in range(folds):
            pts = [(xs[i], ya, zs[i]), (xs[i + 1], ya, zs[i + 1]), (xs[i + 1], yb, zs[i + 1]), (xs[i], yb, zs[i])]
            uv = [((x + width / 2) / width, yy / height) for x, yy, _ in pts]
            add_face(f, pts, tag, uv, (0.0, 0.0, 1.0))
    # The rod, just above the cloth, and its finials and brackets.
    ry, rz = height + 0.02, 0.05
    m = mk.Mesh()
    mk.rod(m, (-width / 2 - 0.12, ry, rz), (width / 2 + 0.12, ry, rz), ROD_R, 6, "metal", 1)
    for sx in (-1.0, 1.0):
        mk.lathe(m, [(0.0, -0.05), (0.045, -0.02), (0.045, 0.02), (0.0, 0.05)], 6, "metal", 2,
                 (sx * (width / 2 + 0.16), ry, rz))
        mk.box(m, (sx * (width / 2 - 0.05) - 0.025, ry - 0.08, 0.0), (sx * (width / 2 - 0.05) + 0.025, ry + 0.02, rz),
               "metal", 1, skip=("-z",))
    hung = from_mesh(m, {1: "iron", 2: "gilt"})
    f.extend(hung.faces)
    p.meta.update(tile=t, wall=(-width / 2 - 0.21, width / 2 + 0.21, 0.0, ry + 0.05))
    return p


# -- the rose window (kit_window_rose_6x6) ---------------------------------------------------------

ROSE_SEG = 48  # points round the light
ROSE_SPOKES = 12
# The moulded ring round the inside face: (from, to beyond the light's radius, proud),
# two orders as the lancets' frame. The light touches the sill, so the ring's foot dips
# below it behind the string course (top at the sill): each order ends where the
# course's profile still stands prouder than it, so nothing shows under the course
# (review of castle M5a: the outer order reached 0.34 m at 0.12 proud, below the course).
ROSE_RING = ((0.0, 0.12, 0.07), (0.12, 0.24, 0.10))


def _ring(r: float, cy: float, n: int = ROSE_SEG) -> list[tuple[float, float]]:
    return [(r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n + 1)]


def _bar(f, a, b, half: float, z0: float, z1: float, tag: str) -> None:
    """A straight tracery bar from a to b (2D), `half` wide each side, its
    front at z1 and sides back to z0."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / n * half, dx / n * half
    q = [(a[0] + nx, a[1] + ny), (b[0] + nx, b[1] + ny), (b[0] - nx, b[1] - ny), (a[0] - nx, a[1] - ny)]
    add_face(f, [(x, y, z1) for x, y in q], tag, [(0.0, 0.0), (n, 0.0), (n, 0.3), (0.0, 0.3)], (0.0, 0.0, 1.0))
    for ((xa, ya), (xb, yb)), sgn in (((q[0], q[1]), 1.0), ((q[2], q[3]), -1.0)):
        add_face(f, [(xa, ya, z0), (xb, yb, z0), (xb, yb, z1), (xa, ya, z1)], tag,
                 [(0.0, 0.0), (n, 0.0), (n, 0.08), (0.0, 0.08)], (sgn * nx, sgn * ny, 0.0))


def rose_window(width: float, height: float, depth: float = G.EXTERIOR_SHELL, glass_at: float = 0.6,
                outer: bool = True) -> Piece:
    """`kit_window_rose_<w>x<h>`: a round light `width` across in a square
    hole `width` x `height` through a wall `depth` thick (y = 0 the hole's
    sill): the wall between the square and the circle on both faces, the
    round reveal, the glass at `glass_at` x depth showing the glass sheet's
    round tracery tile, and stone tracery in front of it: a hub ring, twelve
    spokes, a ring of twelve cusped arcs inside the rim and the rim itself;
    a moulded ring round the inside face. At most grid.ROSE_TRIS."""
    if abs(width - height) > 1e-6:
        raise ValueError("a rose window is round: width == height")
    p = Piece("")
    f = p.faces
    r = width / 2.0
    cy = height / 2.0
    zg = -depth * glass_at
    zf = zg + 0.1
    circle = _ring(r, cy)
    # The wall between the square hole and the circle: a fan from each corner of
    # the square over its quarter of the circle (the quarters meet at the square's
    # sides, where the circle touches them), on the inside face and the outside one.
    q = ROSE_SEG // 4
    corners = ((r, height), (-r, height), (-r, 0.0), (r, 0.0))  # the quarters from 0, 90, 180 and 270 degrees
    for z, facing in ((0.0, 1.0),) + (((-depth, -1.0),) if outer else ()):
        for k, (cx_, cy_) in enumerate(corners):
            arc = circle[k * q: (k + 1) * q + 1]
            geom.fan(f, (cx_, cy_, z), [(x, y, z) for x, y in arc], WALL, (0.0, 0.0, facing))
    # The round reveal (soffit all round), facing the opening's axis.
    for (xa, ya), (xb, yb) in zip(circle, circle[1:]):
        mx, my = (xa + xb) / 2, (ya + yb) / 2
        add_face(f, [(xa, ya, -depth), (xb, yb, -depth), (xb, yb, 0.0), (xa, ya, 0.0)], WALL, None, (-mx, cy - my, 0.0))
    # The glass (both sides), the round tracery tile over its bounding box.
    uv = [((x + r) / width, y / height) for x, y in circle[:-1]]
    add_face(f, [(x, y, zg) for x, y in circle[:-1]], "castle_glass|fit:head_tracery", uv, (0.0, 0.0, 1.0))
    add_face(f, [(x, y, zg) for x, y in reversed(circle[:-1])], "castle_glass|fit:head_tracery",
             [(1.0 - a, b) for a, b in reversed(uv)], (0.0, 0.0, -1.0))
    # Stone tracery in front of the glass.
    frame = openings.trim("window_frame")
    rim_in = 0.86 * r
    hub = 0.17 * r
    spoke_end = rim_in * math.cos(math.pi / ROSE_SEG) - 0.01  # inside the rim's polygon (its chords)

    def band(r0, r1, n=ROSE_SEG, zback=zg, zfront=zf):
        inner = _ring(r0, cy, n)
        outer_ = _ring(r1, cy, n)
        out: list = []
        geom.polyline_band(out, inner, outer_, 0.0, zfront - zback, frame, 0.3, caps=(False, False))
        for face in out:
            face.pts = [(x, y, z + zback) for x, y, z in face.pts]
        f.extend(out)

    band(rim_in, r)  # the rim
    band(hub, hub + 0.12, 24)  # the hub ring
    for k in range(ROSE_SPOKES):
        a = 2 * math.pi * (k + 0.5) / ROSE_SPOKES
        ca, sa = math.cos(a), math.sin(a)
        _bar(f, ((hub + 0.13) * ca, cy + (hub + 0.13) * sa), (spoke_end * ca, cy + spoke_end * sa), 0.055,
             zg, zf, frame)
    # Cusps: an arc between neighbouring spokes just inside the rim (a foil ring).
    for k in range(ROSE_SPOKES):
        a0 = 2 * math.pi * (k + 0.5) / ROSE_SPOKES
        a1 = 2 * math.pi * (k + 1.5) / ROSE_SPOKES
        mid = (a0 + a1) / 2
        rc = rim_in * 0.82
        cx_, cyy = rc * math.cos(mid), cy + rc * math.sin(mid)
        rr = rim_in * math.sin(math.pi / ROSE_SPOKES) * 0.7
        inner = [(cx_ + rr * math.cos(mid + math.pi / 2 + math.pi * i / 8), cyy + rr * math.sin(mid + math.pi / 2 + math.pi * i / 8))
                 for i in range(9)]
        outer_ = [(cx_ + (rr + 0.06) * math.cos(mid + math.pi / 2 + math.pi * i / 8),
                   cyy + (rr + 0.06) * math.sin(mid + math.pi / 2 + math.pi * i / 8)) for i in range(9)]
        out: list = []
        geom.polyline_band(out, inner, outer_, 0.0, 0.07, frame, 0.3, caps=(False, False))
        for face in out:
            face.pts = [(x, y, z + zg) for x, y, z in face.pts]
        f.extend(out)
    # The moulded ring round the inside face (two orders, as the lancets' frame).
    for d0, d1, proud in ROSE_RING:
        r0, r1 = r + d0, r + d1
        inner = _ring(r0, cy)
        outer_ = _ring(r1, cy)
        out: list = []
        geom.polyline_band(out, inner, outer_, 0.0, proud, frame, 0.3, caps=(False, False))
        # It lies on the spandrels inside the square's corners and on the wall
        # beyond its sides; its foot stays behind the string course (ROSE_RING).
        f.extend(out)
    p.meta.update(glass=zg, lights=1, rose=(r, cy))
    return p


# -- the dry well (kit_well_dry) ----------------------------------------------------------------------

WELL_ZONES = {1: "beast_stone", 2: "compass_dark", 3: "iron", 4: "oak_aged", 5: "parcel_tan", 6: "oak"}
KERB_R = 1.1
KERB_IN = 0.82
KERB_H = 0.8
KERB_SIDES = 12


def well_dry() -> Piece:
    """`kit_well_dry`: a dry well on the floor: a twelve-sided stone kerb
    KERB_H high (radius KERB_R, a coping ring a little wider), its dark shaft
    inside, and an iron winch frame (two posts, a crossbar 2.6 m up) with an
    oak roller, a crank, a rope and a bucket. Within grid.WELL_RADIUS of its
    origin (the floor at its centre); players walk through it (the server has
    no prop collision; bots keep out, castle-kit.md)."""
    m = mk.Mesh()
    K, C, I, O, R, B = 1, 2, 3, 4, 5, 6
    ph = math.pi / KERB_SIDES
    mk.lathe(m, [(KERB_R, 0.0), (KERB_R, KERB_H - 0.12)], KERB_SIDES, "stone", K, phase=ph, smooth=False, cap_top=False)
    # The coping: out, up, in, and down the inside to the shaft's dark bottom.
    mk.lathe(m, [(KERB_R, KERB_H - 0.12), (KERB_R + 0.08, KERB_H - 0.1), (KERB_R + 0.08, KERB_H), (KERB_IN, KERB_H)],
             KERB_SIDES, "stone", C, phase=ph, smooth=False, cap_top=False)
    ring = [(KERB_IN * math.sin(ph + 2 * math.pi * k / KERB_SIDES), KERB_IN * math.cos(ph + 2 * math.pi * k / KERB_SIDES)) for k in range(KERB_SIDES + 1)]
    for (xa, za), (xb, zb) in zip(ring, ring[1:]):
        m.poly([(xa, KERB_H, za), (xb, KERB_H, zb), (xb, 0.06, zb), (xa, 0.06, za)], "stone", zone=K)
    m.poly([(x, 0.06, z) for x, z in ring[:-1]], "dark", zone=0)
    # The winch: posts on the coping, the crossbar, the roller, the crank.
    top = 2.62
    for sx in (-1.0, 1.0):
        x = sx * (KERB_R - 0.06)
        mk.box(m, (x - 0.06, KERB_H, -0.06), (x + 0.06, top + 0.12, 0.06), "metal", I, skip=("-y",))
        mk.rod(m, (x, KERB_H + 0.02, 0.0), (x - sx * 0.25, KERB_H + 0.6, 0.0), 0.025, 5, "metal", I)
    mk.box(m, (-KERB_R, top + 0.12, -0.07), (KERB_R, top + 0.24, 0.07), "metal", I)
    mk.rod(m, (-KERB_R + 0.1, top - 0.05, 0.0), (KERB_R - 0.1, top - 0.05, 0.0), 0.09, 8, "wood", O)
    mk.rod(m, (KERB_R + 0.06, top - 0.05, 0.0), (KERB_R + 0.22, top - 0.05, 0.0), 0.022, 5, "metal", I)
    mk.rod(m, (KERB_R + 0.2, top - 0.05, 0.0), (KERB_R + 0.2, top - 0.35, 0.0), 0.02, 5, "metal", I)
    # Rope and bucket hanging into the shaft.
    mk.rod(m, (0.0, top - 0.12, 0.0), (0.0, 1.35, 0.0), 0.018, 5, "fabric", R)
    mk.lathe(m, [(0.13, 0.95), (0.17, 1.28)], 8, "wood", B, cap_top=False)
    mk.lathe(m, [(0.0, 0.95), (0.13, 0.95)], 8, "wood", B, cap_top=False)
    mk.rod(m, (-0.17, 1.28, 0.0), (0.0, 1.38, 0.0), 0.012, 4, "metal", I)
    mk.rod(m, (0.17, 1.28, 0.0), (0.0, 1.38, 0.0), 0.012, 4, "metal", I)
    p = from_mesh(m, WELL_ZONES, walkthrough=True)
    lo, hi = m.bounds()
    p.meta.update(radius=max(math.hypot(x, z) for x, _, z in m.verts), top=hi[1], crossbar=top + 0.12)
    return p
