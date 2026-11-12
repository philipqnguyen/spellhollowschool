"""The pieces of the
Clock Tower Courtyard and the covered bridge (N8), the east wing and the
Hospital Wing (N9) and the Grand Staircase's moving flights.

- `kit_pier_stack_<s>` (replaces M4's builder): the pier stack less the
  corner colonnettes the half-flights 3-4 and 5-6 sweep past as they swing
  round the pier's landings (`args.open`).
- `kit_slab_fascia`: the plain 0.4 m slab edge on a landing edge a gate
  closes (stairs.json `gates`): no balustrade, no corbel table, so a flight
  resting there meets a clean edge and an empty landing shows a cut slab.
- `kit_bed_ward`, `kit_screen_ward`: the Hospital Wing's iron beds (linen,
  a wool blanket, a bedside cabinet) and three-leaf folding screens,
  walk-through like the room furniture set (castle M5b, 26.4).
- `kit_door_locked_<w>x<h>`: the Third-Floor Corridor's locked door: the
  blind door's closed oak pair (openings.blind_door) barred by an iron bar
  in two staples, a padlock on it.
- `kit_clock_face_<d>`: the Clock Tower's face, `d` across: a stone ring,
  the dial, twelve hour marks, two hands (our own design).
- `kit_bridge_covered`: the covered timber bridge (the `bridge` kind): a
  plank deck on two stringers, a timber truss each side (posts, chords,
  braces), railings, tie beams and a shingled gable roof, two stone piers.

Frames as props.json `kit`: origin at the bottom centre, yaw 0 facing +z
(the clock face and the locked door lie on the wall plane z = 0, their
fronts toward +z). Pure Python; colours only through face tags and palette
names (castle_textures.json, the `_doc_p3c_wings_a` block): no colour
literal here."""

from __future__ import annotations

import math

import meshkit as mk
from castle_kit import grid as G
from castle_kit.pieces import geom, hall, openings, staircase
from castle_kit.pieces.geom import Piece, add_face

WALL = "castle_wall|world"
FLOOR = "castle_floor|world"
CEILING = "castle_ceiling|world"

# Zone -> palette name (castle_textures.json `palette`).
IRON, OAK, OAK_AGED, BLANKET, LINEN_Z, LETTER, GILT, SOOT, SHINGLE, BRASS, STONE = range(40, 51)
ZONES = {
    IRON: "iron", OAK: "oak", OAK_AGED: "oak_aged", BLANKET: "ward_blanket", LETTER: "letter_white", GILT: "gilt",
    SOOT: "soot", SHINGLE: "shingle", BRASS: "brass", STONE: "statue_stone",
}

# The props-atlas slots these pieces use (castle_textures.json props_slots; the
# swing nodes' four are rules.SWING_TAGS).
SLOTS = (("fabric", "@ward_blanket"), ("wood", "@shingle"), ("stone", "@gs_flight_stone"),
         ("stone", "@gs_flight_under"), ("metal", "@gs_flight_trim"), ("metal", "@gs_flight_marble"))


def _finish(p: Piece, m: mk.Mesh, lod1: bool = False) -> Piece:
    faces = hall.from_mesh(m, ZONES, walkthrough=p.walkthrough).faces
    (p.lod1 if lod1 else p.faces).extend(faces)
    return p


# =====================================================================================
# The pier stack, its swept corners open
# =====================================================================================


def open_corners(size: float, top: float, mask: float) -> list[tuple[float, float, float]]:
    """The open colonnettes of a pier stack's `open` mask: bit 4 k + i is span k (from the
    k-th pier landing) at staircase.colonnette_spots(size)[i], as (x sign, z sign, y0)."""
    lands = staircase.pier_landings(top)
    m = int(round(mask))
    out = []
    for k, y0 in enumerate(lands[:-1]):
        for i, (cx, cz) in enumerate(staircase.colonnette_spots(size)):
            if m >> (4 * k + i) & 1:
                out.append((math.copysign(1.0, cx), math.copysign(1.0, cz), y0))
    return out


def pier_stack(size: float, top: float = 49.5, open: float = 0) -> Piece:
    """`kit_pier_stack_<s>`: M4's pier stack less the corner
    colonnettes a moving half-flight sweeps past: `open` is a mask of them
    (open_corners; castle_kit.json, checked against stairs.json by the
    tests). A column in the middle of a pier landing would stand in the walk
    from one flight to the other, so the other corners keep theirs (meta
    `colonnettes` the kept spots and spans, `open` the open ones)."""
    p = staircase.pier_stack(size, top)
    lands = p.meta["landings"]
    h = size / 2
    corner = h - 0.6  # the colonnettes stand within 0.45 m of each corner
    gone = open_corners(size, top, open)

    def dropped(pts) -> bool:
        for sx, sz, y0 in gone:
            if all(pt[0] * sx >= corner and pt[2] * sz >= corner and y0 - 1e-6 <= pt[1] for pt in pts):
                nxt = next((y for y in lands if y > y0 + 1e-6), None)
                if nxt is not None and all(pt[1] <= nxt + 1e-6 for pt in pts):
                    return True
        return False

    p.faces = [f for f in p.faces if not dropped(f.pts)]
    p.meta["collider"] = [poly for poly in p.meta["collider"] if not dropped(poly)]
    kept = []
    for y0, y1 in zip(lands, lands[1:]):
        for cx, cz in staircase.colonnette_spots(size):
            if not any(cx * sx > 0 and cz * sz > 0 and abs(y0 - gy) < 1e-6 for sx, sz, gy in gone):
                kept.append((cx, cz, y0, y1 - staircase.SLAB))
    p.meta.update(colonnettes=kept, open=gone)
    return p


# =====================================================================================
# The slab edge under a landing gate (kit_slab_fascia)
# =====================================================================================


def slab_fascia(length: float, drop: float = staircase.SLAB) -> Piece:
    """`kit_slab_fascia`: the landing slab's plain edge `length` long (local
    x 0..length), from the floor (y = 0) down `drop`, on the plane z = 0
    facing +z (the void); the slab's underside is the Planner's."""
    p = Piece("")
    add_face(p.faces, [(0.0, -drop, 0.0), (length, -drop, 0.0), (length, 0.0, 0.0), (0.0, 0.0, 0.0)], WALL, None,
             (0.0, 0.0, 1.0))
    return p


def parapet_coping(length: float, wall: float = 1.0) -> Piece:
    """`kit_parapet_coping`: the coping on a parapet `wall` thick, local x
    0..length along its inner face (z = 0, the wall behind at z < 0), its
    foot on the parapet's top (y = 0): a moulded stone cap overhanging both
    faces by 0.06 m."""
    p = Piece("")
    lo, hi = (0.0, 0.0, -wall - 0.06), (length, 0.2, 0.06)

    def uv(side, pts):
        if side in ("+y", "-y"):
            return [(pt[0], 0.12 + 0.06 * (pt[2] > -wall / 2)) for pt in pts]
        along = 2 if side in ("+x", "-x") else 0
        return [(pt[along], pt[1] / 0.2 * 0.3) for pt in pts]

    geom.box(p.faces, lo, hi, "castle_trim|trim:string", skip=("-y",), uv_fn=uv)
    add_face(p.faces, [(0.0, 0.0, 0.0), (length, 0.0, 0.0), (length, 0.0, 0.06), (0.0, 0.0, 0.06)],
             "castle_trim|trim:string", [(0.0, 0.0), (length, 0.0), (length, 0.03), (0.0, 0.03)], (0.0, -1.0, 0.0))
    add_face(p.faces, [(0.0, 0.0, -wall - 0.06), (length, 0.0, -wall - 0.06), (length, 0.0, -wall), (0.0, 0.0, -wall)],
             "castle_trim|trim:string", [(0.0, 0.0), (length, 0.0), (length, 0.03), (0.0, 0.03)], (0.0, -1.0, 0.0))
    return p


# =====================================================================================
# The Hospital Wing: beds and screens
# =====================================================================================

BED = (1.0, 2.1)  # width x length (m)
BED_TOP = 0.62  # the mattress's top
BED_TRIS = 900
SCREEN_TRIS = 400


def bed_ward(cabinet: float = 1) -> Piece:
    """`kit_bed_ward`: an iron hospital bed, its head against the wall (the
    origin at the head's foot on the wall line, the bed running toward +z):
    head and foot frames of round iron bars, side rails, a linen mattress and
    pillow, a folded-back wool blanket; `cabinet` 1 adds an oak bedside
    cabinet on its left (-x) with a water jug. At most BED_TRIS."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    hw, ln = BED[0] / 2, BED[1]
    z0, z1 = 0.08, 0.08 + ln
    for z, top in ((z0 + 0.03, 1.15), (z1 - 0.03, 0.85)):
        for sx in (-1.0, 1.0):
            mk.rod(m, (sx * hw, 0.0, z), (sx * hw, top, z), 0.025, 6, "metal", IRON)
        for y in (0.42, top - 0.03):
            mk.rod(m, (-hw, y, z), (hw, y, z), 0.018, 6, "metal", IRON, caps=False)
        for k in range(1, 5):
            x = -hw + k * BED[0] / 5
            mk.rod(m, (x, 0.42, z), (x, top - 0.03, z), 0.01, 4, "metal", IRON, caps=False)
    for sx in (-1.0, 1.0):
        mk.rod(m, (sx * hw, 0.42, z0), (sx * hw, 0.42, z1), 0.018, 6, "metal", IRON, caps=False)
    # The mattress, the pillow, the sheet turned back over the blanket.
    mk.box(m, (-hw + 0.03, 0.42, z0 + 0.05), (hw - 0.03, BED_TOP, z1 - 0.05), "linen", LINEN_Z, skip=("-y",))
    mk.box(m, (-hw + 0.12, BED_TOP, z0 + 0.1), (hw - 0.12, BED_TOP + 0.12, z0 + 0.5), "linen", LINEN_Z, skip=("-y",))
    zb = z0 + 0.75
    mk.box(m, (-hw + 0.01, BED_TOP - 0.18, zb), (hw - 0.01, BED_TOP + 0.05, z1 - 0.04), "fabric", BLANKET,
           skip=("-y",))
    mk.box(m, (-hw + 0.01, BED_TOP + 0.05, zb), (hw - 0.01, BED_TOP + 0.07, zb + 0.3), "linen", LINEN_Z,
           skip=("-y",))
    if cabinet:
        cx = -hw - 0.35
        mk.box(m, (cx - 0.25, 0.0, z0), (cx + 0.25, 0.7, z0 + 0.45), "wood", OAK, skip=("-y",))
        mk.box(m, (cx - 0.21, 0.36, z0 + 0.45), (cx + 0.21, 0.62, z0 + 0.47), "wood", OAK_AGED, skip=("-z",))
        mk.lathe(m, [(0.05, 0.7), (0.07, 0.75), (0.06, 0.88), (0.035, 0.94), (0.045, 0.97)], 8, "metal", BRASS,
                 center=(cx + 0.08, 0.0, z0 + 0.22), cap_top=True)
    return _finish(p, m)


def screen_ward(leaves: float = 3) -> Piece:
    """`kit_screen_ward`: a folding screen of `leaves` 0.7 m leaves (oak
    frames, linen panels), 1.8 m tall, zig-zagging along x round its bottom
    centre. At most SCREEN_TRIS."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    n = int(round(leaves))
    w, h = 0.7, 1.8
    x = -n * w * math.cos(0.35) / 2
    for k in range(n):
        a = 0.35 if k % 2 == 0 else -0.35
        dx, dz = w * math.cos(a), w * math.sin(a)
        z = 0.0 if k % 2 == 0 else 0.7 * math.sin(0.35)
        p0, p1 = (x, z), (x + dx, z + dz)
        for (px, pz) in ((p0, p1) if k == 0 else (p1,)):
            mk.rod(m, (px, 0.0, pz), (px, h, pz), 0.025, 4, "wood", OAK)
        for y in (0.15, h - 0.05):
            mk.rod(m, (p0[0], y, p0[1]), (p1[0], y, p1[1]), 0.02, 4, "wood", OAK, caps=False)
        nx, nz = -dz / w, dx / w
        for side in (1.0, -1.0):
            o = 0.006 * side
            pts = [(p0[0] + nx * o, 0.17, p0[1] + nz * o), (p1[0] + nx * o, 0.17, p1[1] + nz * o),
                   (p1[0] + nx * o, h - 0.07, p1[1] + nz * o), (p0[0] + nx * o, h - 0.07, p0[1] + nz * o)]
            if side < 0:
                pts = pts[::-1]
            m.poly(pts, "linen", zone=LINEN_Z)
        x += dx
    return _finish(p, m)


# =====================================================================================
# The Third-Floor Corridor's locked door (kit_door_locked_<w>x<h>)
# =====================================================================================

LOCK_BAR_Y = 1.25  # the bar's centre over the floor


def door_locked(width: float, height: float, depth: float = 0.3) -> Piece:
    """`kit_door_locked_<w>x<h>`: openings.blind_door's closed pair in its
    pointed recess, barred: an iron bar across both leaves in two staples
    and a heavy padlock hanging from the meeting stile's hasp."""
    p = openings.blind_door(width, height, depth)
    m = mk.Mesh()
    z = -depth + 0.06
    half = width / 2 - 0.15
    mk.box(m, (-half, LOCK_BAR_Y - 0.06, z + 0.04), (half, LOCK_BAR_Y + 0.06, z + 0.1), "metal", IRON, skip=("-z",))
    for sx in (-1.0, 1.0):
        x = sx * (half - 0.25)
        mk.box(m, (x - 0.05, LOCK_BAR_Y - 0.11, z), (x + 0.05, LOCK_BAR_Y + 0.11, z + 0.13), "metal", IRON,
               skip=("-z",))
    mk.box(m, (-0.06, LOCK_BAR_Y - 0.14, z + 0.1), (0.06, LOCK_BAR_Y + 0.08, z + 0.13), "metal", IRON, skip=("-z",))
    mk.box(m, (-0.13, LOCK_BAR_Y - 0.42, z + 0.09), (0.13, LOCK_BAR_Y - 0.16, z + 0.17), "metal", BRASS)
    mk.rod(m, (-0.08, LOCK_BAR_Y - 0.16, z + 0.13), (-0.08, LOCK_BAR_Y - 0.05, z + 0.13), 0.02, 4, "metal", IRON)
    mk.rod(m, (0.08, LOCK_BAR_Y - 0.16, z + 0.13), (0.08, LOCK_BAR_Y - 0.05, z + 0.13), 0.02, 4, "metal", IRON)
    mk.rod(m, (-0.08, LOCK_BAR_Y - 0.05, z + 0.13), (0.08, LOCK_BAR_Y - 0.05, z + 0.13), 0.02, 4, "metal", IRON)
    p.faces.extend(hall.from_mesh(m, ZONES).faces)
    return p


# =====================================================================================
# The Clock Tower's face (kit_clock_face_<d>)
# =====================================================================================

CLOCK_HANDS = (10.0, 2.0)  # the hour the hands show (10:10, the clockmaker's smile)


def clock_face(diameter: float, depth: float = 0.25) -> Piece:
    """`kit_clock_face_<d>`: on the wall plane z = 0 facing +z, its centre at
    (0, d / 2): a moulded stone ring standing `depth` proud, the dial a step
    behind its front, twelve gilt hour marks (the quarters longer), iron
    hands at ten past ten and a gilt boss."""
    p = Piece("")
    m = mk.Mesh()
    r = diameter / 2
    cy = r
    n = 48
    ring_in = r * 0.86
    zf = depth

    def circ(rad, k, y_off=0.0):
        a = 2 * math.pi * k / n
        return (rad * math.cos(a), cy + rad * math.sin(a) + y_off)

    for k in range(n):
        (xa, ya), (xb, yb) = circ(r, k), circ(r, k + 1)
        (xc, yc), (xd, yd) = circ(ring_in, k), circ(ring_in, k + 1)
        add_face(p.faces, [(xa, ya, zf), (xc, yc, zf), (xd, yd, zf), (xb, yb, zf)], WALL, None, (0.0, 0.0, 1.0))
        add_face(p.faces, [(xa, ya, 0.0), (xa, ya, zf), (xb, yb, zf), (xb, yb, 0.0)], WALL, None,
                 ((xa + xb) / 2, (ya + yb) / 2 - cy, 0.0))
        add_face(p.faces, [(xc, yc, zf), (xc, yc, zf - 0.08), (xd, yd, zf - 0.08), (xd, yd, zf)], WALL, None,
                 (-(xc + xd) / 2, -((yc + yd) / 2 - cy), 0.0))
    dial = [(x, y, zf - 0.08) for x, y in (circ(ring_in, k) for k in range(n))]
    m.poly(dial, "linen", zone=LINEN_Z)
    zd = zf - 0.08
    for hour in range(12):
        a = math.pi / 2 - 2 * math.pi * hour / 12
        long = hour % 3 == 0
        r0, r1 = ring_in * (0.72 if long else 0.8), ring_in * 0.93
        w = 0.11 if long else 0.07
        c, s = math.cos(a), math.sin(a)
        pts = [(c * r0 - s * w, cy + s * r0 + c * w), (c * r1 - s * w, cy + s * r1 + c * w),
               (c * r1 + s * w, cy + s * r1 - c * w), (c * r0 + s * w, cy + s * r0 - c * w)]
        m.poly([(x, y, zd + 0.02) for x, y in pts[::-1]], "metal", zone=GILT)
    for frac, ln, wd, dz in ((CLOCK_HANDS[0] / 12 + CLOCK_HANDS[1] / 720, 0.5, 0.12, 0.04),
                             (CLOCK_HANDS[1] / 60, 0.78, 0.08, 0.06)):
        a = math.pi / 2 - 2 * math.pi * frac
        c, s = math.cos(a), math.sin(a)
        tip = ring_in * ln
        pts = [(-c * 0.12 - s * wd, cy - s * 0.12 + c * wd), (c * tip, cy + s * tip), (-c * 0.12 + s * wd, cy - s * 0.12 - c * wd)]
        m.poly([(x, y, zd + dz) for x, y in pts[::-1]], "metal", zone=IRON)
    # The boss over the hands' pivot: a lathe about +y, turned to stand out along +z.
    boss = mk.Mesh()
    mk.lathe(boss, [(0.16, 0.0), (0.16, 0.05), (0.08, 0.1)], 8, "metal", GILT, cap_top=True)
    for bf in boss.faces:
        pts = [boss.verts[i] for i in bf.idx]
        m.poly([(x, cy - z, zd + 0.07 + y) for x, y, z in pts], bf.material, zone=bf.zone)
    p.faces.extend(hall.from_mesh(m, ZONES).faces)
    p.meta["centre"] = (0.0, cy)
    return p


# =====================================================================================
# The covered bridge (kit_bridge_covered)
# =====================================================================================

BRIDGE_PANEL = 3.0  # the trusses' panel (post spacing)
BRIDGE_PLATE = 3.3  # the top chord's top over the deck
BRIDGE_RIDGE = 1.6  # the ridge over the plate
BRIDGE_EAVE = 0.45  # the roof beyond the trusses' faces
SHINGLE_COURSE = 0.32  # one course of shingles up the slope
SHINGLE_STEP = 0.035  # each course's butt stands this proud of the next
BRIDGE_TRIS = 26_000


def _bar(f: list, p0, p1, w: float, tag: str) -> None:
    """A square timber `w` thick from p0 to p1 (its centre line)."""
    d = geom.unit(geom.sub(p1, p0))
    ref = (0.0, 1.0, 0.0) if abs(d[1]) < 0.9 else (1.0, 0.0, 0.0)
    a = geom.unit(geom.cross(d, ref))
    b = geom.cross(a, d)
    hw = w / 2
    corners = [(-hw, -hw), (hw, -hw), (hw, hw), (-hw, hw)]

    def at(p, u, v):
        return tuple(p[k] + a[k] * u + b[k] * v for k in range(3))

    for i in range(4):
        (u0, v0), (u1, v1) = corners[i], corners[(i + 1) % 4]
        pts = [at(p0, u0, v0), at(p1, u0, v0), at(p1, u1, v1), at(p0, u1, v1)]
        nrm = tuple(a[k] * (u0 + u1) + b[k] * (v0 + v1) for k in range(3))
        add_face(f, pts, tag, None, nrm)
    for p, sg in ((p0, -1.0), (p1, 1.0)):
        add_face(f, [at(p, u, v) for u, v in corners], tag, None, tuple(sg * d[k] for k in range(3)))


def bridge_covered(length: float = 18.0, width: float = 6.0, rise: float = 2.06, pier_drop: float = 10.0,
                   lod: float = 1) -> Piece:
    """`kit_bridge_covered`: the covered timber bridge along +z from its foot
    (origin: the deck's foot centre, y 0) `length` long, `width` wide, its
    deck rising `rise`: plank deck and stringers, a truss each side (posts
    every BRIDGE_PANEL, top and bottom chords, X braces), a hand rail and a
    knee rail between the posts (the sides open above), tie beams, a
    shingled gable roof (SHINGLE_COURSE courses) with boarded gable ends
    over the ties, two stone piers at the thirds `pier_drop` deep. LOD1
    (`lod`): the deck, chords, posts and a plain roof."""
    p = Piece("", walkthrough=True)
    p.lod1 = [] if lod else None
    f = p.faces
    hw = width / 2
    slope = rise / length
    oak = G.palette_tag("wood", "oak")
    aged = G.palette_tag("wood", "oak_aged")
    shingle = G.palette_tag("wood", "shingle")

    def y_at(z):
        return slope * z

    # The deck: its walked top (the layout's surface), a plank edge each side, stringers under it.
    add_face(f, [(-hw, 0.0, 0.0), (hw, 0.0, 0.0), (hw, rise, length), (-hw, rise, length)], aged,
             [(0.0, 0.0), (width, 0.0), (width, length), (0.0, length)], (0.0, 1.0, -slope))
    add_face(f, [(-hw, -0.12, 0.0), (hw, -0.12, 0.0), (hw, rise - 0.12, length), (-hw, rise - 0.12, length)], aged,
             None, (0.0, -1.0, slope))
    for sx in (-1.0, 1.0):
        x = sx * (hw - 0.15)
        _bar(f, (x, -0.3, -0.1), (x, rise - 0.3, length + 0.1), 0.3, oak)
        _bar(f, (sx * hw * 0.35, -0.32, 0.0), (sx * hw * 0.35, rise - 0.32, length), 0.24, aged)
    for sx in (-1.0, 1.0):
        add_face(f, [(sx * hw, -0.12, 0.0), (sx * hw, rise - 0.12, length), (sx * hw, rise, length), (sx * hw, 0.0, 0.0)],
                 aged, None, (sx, 0.0, 0.0))
    # The trusses: posts into the top chord, X braces (one each side of the truss's plane),
    # a hand rail and a knee rail outside the posts; tie beams and king posts across.
    n = max(1, int(round(length / BRIDGE_PANEL)))
    zs = [k * length / n for k in range(n + 1)]
    chord = BRIDGE_PLATE - 0.13  # the top chord's centre over the deck
    for sx in (-1.0, 1.0):
        x = sx * (hw - 0.15)
        for z in zs:
            zc = min(max(z, 0.12), length - 0.12)
            _bar(f, (x, y_at(zc) - 0.3, zc), (x, y_at(zc) + BRIDGE_PLATE - 0.1, zc), 0.24, oak)
        _bar(f, (x, chord, -0.1), (x, rise + chord, length + 0.1), 0.26, oak)
        for a, b in zip(zs, zs[1:]):
            lo_a, lo_b = y_at(a) + 0.05, y_at(b) + 0.05
            hi_a, hi_b = y_at(a) + BRIDGE_PLATE - 0.3, y_at(b) + BRIDGE_PLATE - 0.3
            _bar(f, (x - 0.045, lo_a, a + 0.15), (x - 0.045, hi_b, b - 0.15), 0.08, aged)
            _bar(f, (x + 0.045, hi_a, a + 0.15), (x + 0.045, lo_b, b - 0.15), 0.08, aged)
            for h, t in ((1.0, 0.1), (0.45, 0.08)):
                _bar(f, (x + sx * 0.17, y_at(a) + h, a + 0.12), (x + sx * 0.17, y_at(b) + h, b - 0.12), t, oak)
    tie = BRIDGE_PLATE - 0.38
    for k, z in enumerate(zs):
        if k % 2:
            continue
        zc = min(max(z, 0.12), length - 0.12)
        _bar(f, (-hw + 0.05, y_at(zc) + tie, zc), (hw - 0.05, y_at(zc) + tie, zc), 0.2, oak)
        _bar(f, (0.0, y_at(zc) + tie + 0.1, zc), (0.0, y_at(zc) + BRIDGE_PLATE + BRIDGE_RIDGE - 0.12, zc), 0.16, aged)
    # The roof: two slopes of shingle courses, boards under, a ridge board, gable ends.
    plate = BRIDGE_PLATE
    eave_x = hw + BRIDGE_EAVE
    eave_drop = BRIDGE_EAVE * BRIDGE_RIDGE / hw
    ridge = plate + BRIDGE_RIDGE
    z_a, z_b = -0.4, length + 0.4
    run = math.hypot(eave_x, BRIDGE_RIDGE + eave_drop)
    courses = max(2, int(round(run / SHINGLE_COURSE)))
    for sx in (-1.0, 1.0):
        for k in range(courses):
            t0, t1 = k / courses, (k + 1) / courses
            xa, xb = sx * eave_x * (1 - t0), sx * eave_x * (1 - t1)
            ya = plate - eave_drop + (BRIDGE_RIDGE + eave_drop) * t0
            yb = plate - eave_drop + (BRIDGE_RIDGE + eave_drop) * t1
            step = SHINGLE_STEP
            pts = [(x, y + slope * z, z) for x, y, z in [(xa, ya + step, z_a), (xb, yb, z_a), (xb, yb, z_b), (xa, ya + step, z_b)]]
            add_face(f, pts, shingle, [(0.0, 0.0), (0.0, SHINGLE_COURSE), (z_b - z_a, SHINGLE_COURSE), (z_b - z_a, 0.0)],
                     (sx, 1.0, 0.0))
            butt = [(xa, ya + slope * z_a, z_a), (xa, ya + step + slope * z_a, z_a), (xa, ya + step + slope * z_b, z_b),
                    (xa, ya + slope * z_b, z_b)]
            add_face(f, butt, shingle, [(0.0, 0.0), (0.0, step), (z_b - z_a, step), (z_b - z_a, 0.0)], (sx, -0.3, 0.0))
        under = [(sx * eave_x, plate - eave_drop - 0.06 + slope * z_a, z_a), (0.0, ridge - 0.06 + slope * z_a, z_a),
                 (0.0, ridge - 0.06 + slope * z_b, z_b), (sx * eave_x, plate - eave_drop - 0.06 + slope * z_b, z_b)]
        add_face(f, under, oak, None, (-sx * BRIDGE_RIDGE, -hw, 0.0))
        for z in (z_a, z_b):
            sg = -1.0 if z == z_a else 1.0
            edge = [(sx * eave_x, plate - eave_drop - 0.06 + slope * z, z), (sx * eave_x, plate - eave_drop + SHINGLE_STEP + slope * z, z),
                    (0.0, ridge + slope * z, z), (0.0, ridge - 0.06 + slope * z, z)]
            add_face(f, edge, aged, None, (0.0, 0.0, sg))
    _bar(f, (0.0, ridge + 0.02 + slope * z_a, z_a), (0.0, ridge + 0.02 + slope * z_b, z_b), 0.14, aged)
    for z, sg in ((0.12, -1.0), (length - 0.12, 1.0)):
        y0 = y_at(z) + plate - 0.1
        add_face(f, [(-hw + 0.15, y0, z + sg * 0.13), (hw - 0.15, y0, z + sg * 0.13),
                     (0.0, y_at(z) + ridge - 0.12, z + sg * 0.13)], aged, None, (0.0, 0.0, sg))
    # Two stone piers under the deck at the thirds.
    for zc in (length / 3, 2 * length / 3):
        y = y_at(zc) - 0.32
        geom.box(f, (-hw * 0.55, y - pier_drop, zc - 0.7), (hw * 0.55, y, zc + 0.7), WALL, skip=("-y",))
    if lod:
        lf = p.lod1
        add_face(lf, [(-hw, 0.0, 0.0), (hw, 0.0, 0.0), (hw, rise, length), (-hw, rise, length)], aged,
                 [(0.0, 0.0), (width, 0.0), (width, length), (0.0, length)], (0.0, 1.0, -slope))
        for sx in (-1.0, 1.0):
            x = sx * (hw - 0.15)
            for z in zs:
                zc = min(max(z, 0.12), length - 0.12)
                _bar(lf, (x, y_at(zc) - 0.3, zc), (x, y_at(zc) + BRIDGE_PLATE - 0.1, zc), 0.24, oak)
            _bar(lf, (x, -0.3, -0.1), (x, rise - 0.3, length + 0.1), 0.3, oak)
            _bar(lf, (x, BRIDGE_PLATE - 0.13, -0.1), (x, rise + BRIDGE_PLATE - 0.13, length + 0.1), 0.26, oak)
            top = [(sx * eave_x, plate - eave_drop + slope * z_a, z_a), (0.0, ridge + slope * z_a, z_a),
                   (0.0, ridge + slope * z_b, z_b), (sx * eave_x, plate - eave_drop + slope * z_b, z_b)]
            add_face(lf, top, shingle, None, (sx * BRIDGE_RIDGE, hw, 0.0))
            add_face(lf, [(sx * eave_x, plate - eave_drop - 0.06 + slope * z_a, z_a), (0.0, ridge - 0.06 + slope * z_a, z_a),
                          (0.0, ridge - 0.06 + slope * z_b, z_b), (sx * eave_x, plate - eave_drop - 0.06 + slope * z_b, z_b)],
                     oak, None, (-sx * BRIDGE_RIDGE, -hw, 0.0))
        for zc in (length / 3, 2 * length / 3):
            y = y_at(zc) - 0.32
            geom.box(lf, (-hw * 0.55, y - pier_drop, zc - 0.7), (hw * 0.55, y, zc + 0.7), WALL, skip=("-y",))
    p.meta.update(deck=(width, length, rise))
    return p


BUILDERS = {
    "kit_slab_fascia": slab_fascia,
    "kit_parapet_coping": parapet_coping,
    "kit_bed_ward": bed_ward,
    "kit_screen_ward": screen_ward,
    "kit_bridge_covered": bridge_covered,
}
BUILDERS_SIZED = {
    "kit_pier_stack": lambda w, h, v, **kw: pier_stack(w, **kw),  # (replaces staircase.pier_stack)
    "kit_door_locked": lambda w, h, v, **kw: door_locked(w, h, **kw),
    "kit_clock_face": lambda w, h, v, **kw: clock_face(w, **kw),
}
CATALOGUE = {
    "kit_slab_fascia": {"length": 4.0},
    "kit_parapet_coping": {"length": 6.0},
    "kit_bed_ward": {"cabinet": 1},
    "kit_screen_ward": {"leaves": 3},
    "kit_door_locked_3x4.5": {},
    "kit_clock_face_7": {},
    "kit_bridge_covered": {},
}
