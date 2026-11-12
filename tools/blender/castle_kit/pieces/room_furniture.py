"""The room furniture set (castle M5b, docs/art/castle-kit.md "Pieces"): sofas,
the armchair, rugs, the round and study tables, the study chair, the
noticeboard, the closed dorm door with its crest and the ring chandelier.
Shared by every M5b room (the common rooms, the library, the Kitchens).
Human scale: seats SEAT_TOP (0.46 m) up, tables 0.74-0.76.

Frames as props.json `kit`: origin at the bottom centre, yaw 0 facing +z
(a seat faces +z; the noticeboard's and the door's backs lie on the wall
plane z = 0, their faces towards +z). Players walk through the furniture
(`walkthrough`); the two wall pieces stand at most 0.15 m off the wall.

Simple shapes that read under the toon ramp and the baked AO (the castle
has no outline): cushions, backs and arms as separate blocks with real
relief, table tops with thickness, rolled arms and backs.

Colours come only from palette data: oak from the wood sheet
(castle_wood, the hall furniture's strip UVs) and the props atlas's `oak`;
cloth, leather, cork, notices, metals by palette name. House accents only
by the names house_a..d, chosen by an `args` index (grid.FURNITURE_COVERS,
grid.RUG_FIELDS, the door's `house`).

Pieces carry `meta["seats"]` ([x0, z0, x1, z1, y, yaw, front, back, drop],
17.4; hall_furniture.py) and
`meta["floor_seats"]` ([x0, z0, x1, z1, y], 26.5) in their own frame;
assemble.py turns them into the manifest's rows."""

from __future__ import annotations

import math

import meshkit as mk
from castle_kit import grid as G
from castle_kit.pieces import geom, hall
from castle_kit.pieces.geom import Piece, add_face
from castle_kit.pieces.hall_furniture import PLANKS, _chair, wbox

# Zone -> palette name (castle_textures.json `palette`).
COVER0 = 11  # + args.cover: FURNITURE_COVERS (house_a..d, upholstery_leather, upholstery_rose)
FIELD0 = 21  # + args.field: RUG_FIELDS (house_a..d, rug_neutral)
BORDER, CORK, NOTE_A, NOTE_B, IRON, GILT, BRASS, OAK, CUSHION = 30, 31, 32, 33, 34, 35, 36, 37, 38
ZONES = {
    **{COVER0 + i: name for i, name in enumerate(G.FURNITURE_COVERS)},
    **{FIELD0 + i: name for i, name in enumerate(G.RUG_FIELDS)},
    BORDER: "rug_border", CORK: "cork", NOTE_A: "letter_white", NOTE_B: "parcel_tan", IRON: "iron", GILT: "gilt",
    BRASS: "brass", OAK: "oak", CUSHION: "wine_red",
}
WAX = "castle_props|kit:wax"


def _cover(cover: float) -> tuple[str, int]:
    """(kit tile, zone) of an upholstery cover index: cloth for the house
    colours, leather for the leather cover."""
    i = int(round(cover))
    if not 0 <= i < len(G.FURNITURE_COVERS):
        raise ValueError(f"cover must be 0..{len(G.FURNITURE_COVERS) - 1}")
    return ("leather" if G.FURNITURE_COVERS[i] == "upholstery_leather" else "fabric"), COVER0 + i


def _finish(p: Piece, m: mk.Mesh) -> Piece:
    p.faces.extend(hall.from_mesh(m, ZONES, walkthrough=p.walkthrough).faces)
    return p


def _row(x0: float, x1: float, y: float = G.SEAT_TOP, front: float | None = None, back: float | None = None) -> list:
    """A seat row facing +z, its hips' band centred on z = 0; the seat
    reaching `front` ahead (the cushions' front edge) and `back` behind (the
    back cushion's face), its top `y` over the floor."""
    hd = 0.22
    front = SEAT_FRONT if front is None else front
    back = -BACK_FACE if back is None else back
    return [round(x0, 4), -hd, round(x1, 4), hd, y, 0.0, round(front, 4), round(back, 4), y]


# -- sofas and the armchair ------------------------------------------------------------------

SEAT_FRONT = 0.40  # the cushions' front edge (= grid.SEAT_FRONT_MAX ahead of the row's centre line)
BACK_FACE = -0.25  # the back cushion's face
DEPTH_BACK = -0.45  # the back's rear


def _upholstered(m: mk.Mesh, tile: str, zone: int, hw: float, arm: float, back_h: float, cushions: int,
                 wings: bool = False) -> None:
    """A sofa or armchair body centred on x, `hw` half-wide over its arms:
    feet, a base, seat cushions, a back with a rolled top, arms with rolled
    tops (and wings on an armchair)."""
    for sx in (-1.0, 1.0):
        for z in (DEPTH_BACK + 0.06, SEAT_FRONT - 0.08):
            x = sx * (hw - 0.07)
            mk.box(m, (x - 0.04, 0.0, z - 0.04), (x + 0.04, 0.08, z + 0.04), "wood", OAK, skip=("-y", "+y"))
    # The base under the cushions (a step back from their front, so the cushions overhang).
    mk.box(m, (-hw, 0.08, DEPTH_BACK), (hw, 0.30, SEAT_FRONT - 0.03), tile, zone, skip=("-y",))
    # Seat cushions, a little gap between them.
    inner0, inner1 = -hw + arm, hw - arm
    w = (inner1 - inner0) / cushions
    for i in range(cushions):
        a, b = inner0 + i * w + 0.01, inner0 + (i + 1) * w - 0.01
        mk.box(m, (a, 0.30, BACK_FACE + 0.01), (b, G.SEAT_TOP, SEAT_FRONT), tile, zone, skip=("-y",))
    # The back with a rolled top.
    # (Its ends run into the arms: no end face shares an arm's plane.)
    mk.box(m, (inner0, 0.30, DEPTH_BACK), (inner1, back_h - 0.1, BACK_FACE), tile, zone, skip=("-y", "-x", "+x"))
    zr = (DEPTH_BACK + BACK_FACE) / 2
    mk.rod(m, (inner0 - 0.05, back_h - 0.1, zr), (inner1 + 0.05, back_h - 0.1, zr), 0.11, 6, tile, zone)
    # Arms with rolled tops.
    for sx in (-1.0, 1.0):
        x0, x1 = sorted((sx * hw, sx * (hw - arm)))
        mk.box(m, (x0, 0.30, DEPTH_BACK), (x1, 0.58, SEAT_FRONT - 0.02), tile, zone, skip=("-y",))
        xc = (x0 + x1) / 2
        mk.rod(m, (xc, 0.60, DEPTH_BACK - 0.02), (xc, 0.60, SEAT_FRONT), arm / 2 + 0.02, 6, tile, zone)
        if wings:
            mk.box(m, (x0, 0.62, DEPTH_BACK), (x1, back_h - 0.15, -0.12), tile, zone)


def sofa(length: float, cover: float = 0) -> Piece:
    """`kit_sofa_<len>` (1.6-3.2 m): a sofa facing +z; one seat row of
    `len // SEAT_WIDTH_MIN` sitters between its arms; at most SOFA_TRIS."""
    if not G.SOFA_LENGTH[0] - 1e-9 <= length <= G.SOFA_LENGTH[1] + 1e-9:
        raise ValueError(f"kit_sofa length must be {G.SOFA_LENGTH[0]}..{G.SOFA_LENGTH[1]} m")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    tile, zone = _cover(cover)
    hw, arm = length / 2.0, 0.2
    inner = length - 2 * arm
    n = max(1, int(inner // G.SEAT_WIDTH_MIN))
    _upholstered(m, tile, zone, hw, arm, 0.95, max(1, min(n, 3)))
    p.meta.update(length=length, sitters=n, arm_gap=round(inner, 4), height=0.95 + 0.01,
                  seats=[_row(-hw + arm, hw - arm)])
    return _finish(p, m)


def armchair(cover: float = 0) -> Piece:
    """`kit_armchair`: a wing armchair facing +z, its arms ARM_GAP_MIN or
    more apart; one seat; at most ARMCHAIR_TRIS."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    tile, zone = _cover(cover)
    hw, arm = 0.5, 0.2
    _upholstered(m, tile, zone, hw, arm, 1.15, 1, wings=True)
    p.meta.update(arm_gap=round(2 * (hw - arm), 4), height=1.16, seats=[_row(-hw + arm, hw - arm)])
    return _finish(p, m)


# -- rugs ---------------------------------------------------------------------------------------

RUG_BORDER = 0.25  # the border band's width


def rug(w: float, d: float, field: float = 4) -> Piece:
    """`kit_rug_<w>x<d>` (1.5-8 m each way): a rug RUG_THICK thick, its
    field in RUG_FIELDS[args.field] inside a `rug_border` band; one floor
    seat (the rug less 0.1 m round). At most RUG_TRIS."""
    lo, hi = G.RUG_SIZE
    if not (lo - 1e-9 <= w <= hi + 1e-9 and lo - 1e-9 <= d <= hi + 1e-9):
        raise ValueError(f"kit_rug sizes must be {lo}..{hi} m")
    i = int(round(field))
    if not 0 <= i < len(G.RUG_FIELDS):
        raise ValueError(f"field must be 0..{len(G.RUG_FIELDS) - 1}")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    t = G.RUG_THICK
    hw, hd, b = w / 2.0, d / 2.0, RUG_BORDER
    # The field and the border band on top (no shared edges overlap), the edge all round.
    m.poly([(-hw + b, t, hd - b), (hw - b, t, hd - b), (hw - b, t, -hd + b), (-hw + b, t, -hd + b)], "fabric", zone=FIELD0 + i)
    for (x0, z0, x1, z1) in ((-hw, -hd, hw, -hd + b), (-hw, hd - b, hw, hd), (-hw, -hd + b, -hw + b, hd - b),
                             (hw - b, -hd + b, hw, hd - b)):
        m.poly([(x0, t, z1), (x1, t, z1), (x1, t, z0), (x0, t, z0)], "fabric", zone=BORDER)
    mk.box(m, (-hw, 0.0, -hd), (hw, t, hd), "fabric", BORDER, skip=("-y", "+y"))
    p.meta.update(size=(w, d), height=t, floor_seats=[[round(-hw + 0.1, 4), round(-hd + 0.1, 4), round(hw - 0.1, 4),
                                                         round(hd - 0.1, 4), t]])
    return _finish(p, m)


# -- tables and the study chair -----------------------------------------------------------------


def table_round(diameter: float = 1.4, height: float = 0.74) -> Piece:
    """`kit_table_round`: an oak table 1.4 m across, top 0.74, on a turned
    pedestal with four splayed feet; at most TABLE_ROUND_TRIS."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    r = diameter / 2.0
    mk.lathe(m, [(0.0, height - 0.05), (r, height - 0.05), (r, height), (0.0, height)], 16, "wood", OAK, cap_top=False)
    mk.lathe(m, [(0.06, 0.1), (0.1, 0.18), (0.07, 0.3), (0.08, 0.55), (0.13, height - 0.12), (0.2, height - 0.05)], 8,
             "wood", OAK, cap_top=False)
    for k in range(4):
        a = math.pi / 4 + k * math.pi / 2
        mk.rod(m, (0.05 * math.cos(a), 0.14, 0.05 * math.sin(a)), (0.45 * math.cos(a), 0.02, 0.45 * math.sin(a)), 0.04, 4,
               "wood", OAK)
    p.meta.update(diameter=diameter, height=height)
    return _finish(p, m)


def table_study(length: float = 2.4, depth: float = 0.9, height: float = 0.76) -> Piece:
    """`kit_table_study`: a reading table long along local x (args.length
    1.6-3.2 m), 0.9 deep, top 0.76 (0.06 thick), square legs and a shallow
    apron set back from the edge (knee room under it); at most
    TABLE_STUDY_TRIS."""
    if not 1.6 - 1e-9 <= length <= 3.2 + 1e-9:
        raise ValueError("kit_table_study length must be 1.6..3.2 m")
    p = Piece("", walkthrough=True)
    f = p.faces
    hl, hd = length / 2.0, depth / 2.0
    top0 = height - 0.06
    wbox(f, (-hl, top0, -hd), (hl, height, hd))
    for sx in (-1.0, 1.0):
        for sz in (-1.0, 1.0):
            x, z = sx * (hl - 0.12), sz * (hd - 0.12)
            wbox(f, (x - 0.045, 0.0, z - 0.045), (x + 0.045, top0, z + 0.045), skip=("+y",), axis=2)
    for sz in (-1.0, 1.0):  # the long aprons, 0.06 high under the top
        z0, z1 = sorted((sz * (hd - 0.14), sz * (hd - 0.1)))
        wbox(f, (-hl + 0.17, top0 - 0.06, z0), (hl - 0.17, top0, z1), skip=("+y", "+x", "-x"))
    for sx in (-1.0, 1.0):
        x0, x1 = sorted((sx * (hl - 0.14), sx * (hl - 0.1)))
        wbox(f, (x0, top0 - 0.06, -hd + 0.17), (x1, top0, hd - 0.17), skip=("+y", "+z", "-z"), axis=2)
    p.meta.update(length=length, depth=depth, height=height)
    return p


def chair_study() -> Piece:
    """`kit_chair_study`: a plain oak chair facing +z (the hall chair
    without finials or arms, its cushion's top at SEAT_TOP); one seat; at
    most CHAIR_STUDY_TRIS."""
    from castle_kit.pieces import hall_furniture as HF

    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    _chair(p.faces, m, 0.0, width=0.56, back_h=1.1, seat=G.SEAT_TOP - 0.06, finials=False, gable=0.14)
    p.faces.extend(hall.from_mesh(m, HF.ZONES, walkthrough=True).faces)
    p.meta.update(height=1.1, seats=[_row(-0.28, 0.28, front=HF.CHAIR_FRONT, back=HF.CHAIR_BACK)])
    return p


# -- wall pieces: the noticeboard and the dorm door ---------------------------------------------

BOARD = (1.8, 1.2)  # w x h of the cork
BOARD_FOOT = 1.1
NOTES = ((-0.6, 0.15, 0.36, 0.48, NOTE_A), (-0.15, 0.55, 0.3, 0.4, NOTE_B), (0.32, 0.12, 0.42, 0.3, NOTE_A),
         (0.25, 0.62, 0.34, 0.44, NOTE_A), (-0.62, 0.72, 0.3, 0.3, NOTE_B))  # (x, y up the cork, w, h, zone)


def noticeboard() -> Piece:
    """`kit_noticeboard`: a 1.8 x 1.2 m cork board in an oak frame on the
    wall, its foot 1.1 m up, five notices pinned (paper colours by palette
    name, no text); at most NOTICEBOARD_TRIS."""
    p = Piece("")
    m = mk.Mesh()
    f = p.faces
    bw, bh = BOARD
    x0, x1, y0, y1 = -bw / 2, bw / 2, BOARD_FOOT, BOARD_FOOT + bh
    fr = 0.08
    m.poly([(x0, y0, 0.03), (x1, y0, 0.03), (x1, y1, 0.03), (x0, y1, 0.03)], "fabric", zone=CORK)
    wbox(f, (x0 - fr, y0 - fr, 0.0), (x1 + fr, y0, 0.07), skip=("-z",))
    wbox(f, (x0 - fr, y1, 0.0), (x1 + fr, y1 + fr, 0.07), skip=("-z",))
    wbox(f, (x0 - fr, y0, 0.0), (x0, y1, 0.07), skip=("-z", "+y", "-y"), axis=2)
    wbox(f, (x1, y0, 0.0), (x1 + fr, y1, 0.07), skip=("-z", "+y", "-y"), axis=2)
    for x, y, w, h, zone in NOTES:
        mk.box(m, (x - w / 2, y0 + y, 0.03), (x + w / 2, y0 + y + h, 0.036), "fabric", zone, skip=("-z",))
        mk.box(m, (x - 0.015, y0 + y + h - 0.05, 0.036), (x + 0.015, y0 + y + h - 0.02, 0.05), "metal", BRASS, skip=("-z",))
    p.meta.update(wall=(x0 - fr, x1 + fr, y0 - fr, y1 + fr), proud=0.07, height=y1 + fr)
    return _finish(p, m)


DOOR_LEAF = (1.6, 2.6)
DOOR_FRAME = (2.0, 2.9)
DOOR_PROUD = 0.15


def _shield(m: mk.Mesh, cx: float, cy: float, w: float, h: float, z0: float, z1: float, zone: int) -> None:
    """A heater shield outline (flat top, pointed foot) extruded on the wall."""
    hw = w / 2.0
    outline = [(cx - hw, cy + h * 0.5), (cx - hw, cy - h * 0.05), (cx - hw * 0.55, cy - h * 0.38), (cx, cy - h * 0.5),
               (cx + hw * 0.55, cy - h * 0.38), (cx + hw, cy - h * 0.05), (cx + hw, cy + h * 0.5)]
    mk.prism(m, list(reversed(outline)) if mk.polygon_area(outline) < 0 else outline, z0, z1,
             "metal" if zone in (GILT, BRASS, IRON) else "fabric", zone, back=False)


def door_dorm(house: float = 0, round: float = 0) -> Piece:  # noqa: A002 (castle_kit.json args name)
    """`kit_door_dorm`: a closed oak dormitory door on the wall face: a
    1.6 x 2.6 m planked leaf (or, `round` 1, a round leaf 2.2 m across) in
    an oak frame 2 x 2.9 m, at most DOOR_PROUD off the wall; a crest plate
    in house_a..d (`house` 0-3) with a gilt rim, an iron handle. At
    most DORM_DOOR_TRIS. Not walk-through: within INTRUSION_MAX of the wall
    below 2.4 m."""
    h = int(round_(house))
    if not 0 <= h <= 3:
        raise ValueError("house must be 0..3")
    p = Piece("")
    m = mk.Mesh()
    f = p.faces
    fw, fh = DOOR_FRAME
    if round_(round) >= 1:
        rr = 1.1
        cy = rr + 0.02
        ring = [(rr * math.cos(2 * math.pi * k / 20), cy + rr * math.sin(2 * math.pi * k / 20)) for k in range(20)]
        outer = [(1.3 * x, cy + 1.3 * (y - cy)) for x, y in ring]
        # The frame: a band of quads between the two rings (flat, 0.12 proud), the leaf inside (0.08 proud).
        for k in range(20):
            a, b = ring[k], ring[(k + 1) % 20]
            c, d = outer[(k + 1) % 20], outer[k]
            add_face(f, [(a[0], a[1], 0.12), (b[0], b[1], 0.12), (c[0], c[1], 0.12), (d[0], d[1], 0.12)], PLANKS,
                     [(a[0], a[1]), (b[0], b[1]), (c[0], c[1]), (d[0], d[1])], (0.0, 0.0, 1.0))
            add_face(f, [(a[0], a[1], 0.08), (b[0], b[1], 0.08), (b[0], b[1], 0.12), (a[0], a[1], 0.12)], PLANKS,
                     [(0.0, 0.0), (0.2, 0.0), (0.2, 0.04), (0.0, 0.04)], None)
            add_face(f, [(d[0], d[1], 0.0), (c[0], c[1], 0.0), (c[0], c[1], 0.12), (d[0], d[1], 0.12)], PLANKS,
                     [(0.0, 0.0), (0.2, 0.0), (0.2, 0.12), (0.0, 0.12)], None)
        add_face(f, [(x, y, 0.08) for x, y in ring], PLANKS, [(x, y) for x, y in ring], (0.0, 0.0, 1.0))
        crest_y, handle = cy + 0.25, (0.6, cy - 0.1)
        span = (-1.3 * rr, 1.3 * rr, 0.0, cy + 1.3 * rr)
    else:
        lw, lh = DOOR_LEAF
        fr = (fw - lw) / 2
        # The leaf: vertical planks (the wood sheet's door planks), 0.08 proud.
        add_face(f, [(-lw / 2, 0.0, 0.08), (lw / 2, 0.0, 0.08), (lw / 2, lh, 0.08), (-lw / 2, lh, 0.08)], PLANKS,
                 [(-lw / 2, 0.0), (lw / 2, 0.0), (lw / 2, lh), (-lw / 2, lh)], (0.0, 0.0, 1.0))
        # The frame: jambs and a head, 0.12 proud (its inner faces close the leaf's step).
        wbox(f, (-fw / 2, 0.0, 0.0), (-lw / 2, lh, 0.12), skip=("-z", "-y"), axis=2)
        wbox(f, (lw / 2, 0.0, 0.0), (fw / 2, lh, 0.12), skip=("-z", "-y"), axis=2)
        wbox(f, (-fw / 2, lh, 0.0), (fw / 2, fh, 0.12), skip=("-z",))
        # Two iron straps across the leaf.
        for y in (0.45, lh - 0.55):
            mk.box(m, (-lw / 2 + 0.06, y, 0.08), (lw / 2 - 0.06, y + 0.08, 0.1), "metal", IRON, skip=("-z",))
        crest_y, handle = 1.75, (0.55, 1.0)
        span = (-fw / 2, fw / 2, 0.0, fh)
        _ = fr
    # The crest: a gilt shield with the house colour inside, over the leaf.
    _shield(m, 0.0, crest_y, 0.56, 0.68, 0.08, 0.12, GILT)
    _shield(m, 0.0, crest_y + 0.01, 0.46, 0.56, 0.12, 0.135, COVER0 + h)
    # The handle: an iron plate and a knob.
    hx, hy = handle
    mk.box(m, (hx - 0.06, hy - 0.09, 0.08), (hx + 0.06, hy + 0.09, 0.1), "metal", IRON, skip=("-z",))
    mk.box(m, (hx - 0.025, hy - 0.025, 0.1), (hx + 0.025, hy + 0.025, 0.14), "metal", IRON, skip=("-z",))
    p.meta.update(wall=span, proud=DOOR_PROUD, house=G.FURNITURE_COVERS[h], round=bool(round_(round) >= 1))
    return _finish(p, m)


def round_(v: float) -> int:
    return int(math.floor(float(v) + 0.5))


# -- the ring chandelier ---------------------------------------------------------------------------

CANDLE_R = 0.035
CANDLE_H = 0.24
FLAME_H = 0.12


def _candle(f: list, x: float, y: float, z: float, h: float = CANDLE_H) -> tuple:
    """A hexagonal wax candle on (x, y, z) (the floating candles' form,
    smaller): 20 triangles. Returns its flame (x, y, z, height): the flame
    is the client's animated fire (docs/architecture.md "Rendering")."""
    ring0 = [(x + CANDLE_R * math.cos(math.pi * k / 3), y, z + CANDLE_R * math.sin(math.pi * k / 3)) for k in range(6)]
    ring1 = [(px, y + h, pz) for px, _, pz in ring0]
    for k in range(6):
        a, b = ring0[k], ring0[(k + 1) % 6]
        c, d = ring1[(k + 1) % 6], ring1[k]
        mx, mz = (a[0] + b[0]) / 2 - x, (a[2] + b[2]) / 2 - z
        add_face(f, [a, b, c, d], WAX, [(0.0, 0.0), (0.03, 0.0), (0.03, h), (0.0, h)], (mx, 0.0, mz))
    add_face(f, ring1, WAX, None, (0.0, 1.0, 0.0))
    return (round(x, 4), round(y + h + 0.005, 4), round(z, 4), FLAME_H)


def chandelier_ring(radius: float = 1.0, count: float = 12, chain: float = 3.0, group: float = 0, amp: float = 0.0,
                    period: float = 0.0, phase: float = 0.0) -> Piece:
    """`kit_chandelier_ring`: an iron ring of `radius` (0.6-1.5 m) in the
    plane y = 0 with `count` (6-24) candles on drip cups round it, four
    spokes to a hub, a pendant CHANDELIER_BELOW under the ring, three
    chains to a crown 0.9 m up and one chain on to `chain` metres above the
    ring (the ceiling). It hangs in its own candle group (args.group,
    schema._resolve_chandeliers; `group`, `amp`, `period` and `phase` are the
    group's, not the geometry's). At most CHANDELIER_TRIS."""
    lo, hi = G.CHANDELIER_RADIUS
    n = int(round(count))
    if not lo - 1e-9 <= radius <= hi + 1e-9 or not G.CHANDELIER_COUNT[0] <= n <= G.CHANDELIER_COUNT[1]:
        raise ValueError(f"kit_chandelier_ring: radius {lo}..{hi} m, count {G.CHANDELIER_COUNT[0]}..{G.CHANDELIER_COUNT[1]}")
    crown = 0.9
    if chain < crown + 0.1:
        raise ValueError(f"kit_chandelier_ring: chain must be at least {crown + 0.1} m")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    r, t = radius, 0.05
    mk.lathe(m, [(r - t, -t), (r + t, -t), (r + t, t), (r - t, t), (r - t, -t)], 24, "metal", IRON, cap_top=False)
    for k in range(4):
        a = math.pi / 4 + k * math.pi / 2
        mk.rod(m, (0.1 * math.cos(a), 0.0, 0.1 * math.sin(a)), ((r - t) * math.cos(a), 0.0, (r - t) * math.sin(a)), 0.02, 4,
               "metal", IRON, caps=False)
    mk.lathe(m, [(0.0, -G.CHANDELIER_BELOW), (0.05, -0.22), (0.11, -0.08), (0.12, 0.04), (0.06, 0.1), (0.0, 0.12)], 8,
             "metal", IRON, cap_top=False)
    for k in range(3):
        a = k * 2 * math.pi / 3
        mk.rod(m, (r * math.cos(a), t, r * math.sin(a)), (0.0, crown, 0.0), 0.012, 4, "metal", IRON, caps=False)
    mk.rod(m, (0.0, crown, 0.0), (0.0, chain, 0.0), 0.02, 4, "metal", IRON, caps=False)
    mk.lathe(m, [(0.0, crown - 0.04), (0.06, crown), (0.0, crown + 0.06)], 6, "metal", GILT, cap_top=False)
    flames = []
    for k in range(n):
        a = 2 * math.pi * (k + 0.5) / n
        x, z = r * math.cos(a), r * math.sin(a)
        mk.lathe(m, [(0.0, t), (0.07, t + 0.015), (0.06, t + 0.035)], 6, "metal", BRASS, (x, 0.0, z), cap_top=True)
        flames.append(_candle(p.faces, x, t + 0.035, z))
    p.meta.update(radius=radius, count=n, chain=chain, lowest=-G.CHANDELIER_BELOW, top=chain, candles=flames)
    return _finish(p, m)
