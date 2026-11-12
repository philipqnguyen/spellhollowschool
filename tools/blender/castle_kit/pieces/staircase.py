"""The Grand Staircase's pieces (castle kit M4, docs/art/castle-kit.md):
the gallery edge along every railing run (a fascia and
cornice under the slab edge, a corbel table, the pierced balustrade), the
corbelled landings, the pier stack with its corner colonnettes, the hanging
flights with stepped undersides, the skylight lantern and the floor compass.
Frames as in geom.py (x along the run or across the piece, y up, z out of
the wall face or, for edges and landings, into the walkable side / the void
as each builder says). Pure Python; colours only through face tags (trim
strips and tiles, `castle_props` palette names): no colour literal here
(docs/art/castle-kit.md "Colours come from palette data only")."""

from __future__ import annotations

import math

from castle_kit import grid as G
from castle_kit.pieces import geom, mouldings, stairs
from castle_kit.pieces.geom import Piece, add_face

WALL = "castle_wall|world"
FLOOR = "castle_floor|world"
CEILING = "castle_ceiling|world"
GLASS = "castle_glass|fit:quarries"
BRASS = G.palette_tag("metal", "brass")
COMPASS_DARK = G.palette_tag("stone", "compass_dark")
COMPASS_LIGHT = G.palette_tag("stone", "compass_light")
# The props-atlas slots these pieces use (castle_textures.json props_slots).
SLOTS = (("metal", "@brass"), ("stone", "@compass_dark"), ("stone", "@compass_light"))


def trim(strip: str) -> str:
    return f"castle_trim|trim:{strip}"


def fit(tile: str) -> str:
    return f"castle_trim|fit:{tile}"


# -- closed-profile sweeps -------------------------------------------------------------


def loop_sweep(faces, loop, x0: float, x1: float, tag: str, metres: float, ends=("cap", "cap"), hide=(),
               cap_tag: str | None = None) -> None:
    """A closed profile [(z, y), ...] (counter-clockwise with z to the
    right and y up, so every face points out of the body) swept along x
    from x0 to x1; segment i (from point i to i + 1) is left out when i is
    in `hide` (faces that would lie on the slab or the fascia behind).
    Ends as geom.sweep: "cap", "miter" (shortened by z, for a corner whose
    other run lies on the +z side), "outer" (shortened by -z: both runs'
    bodies reach into the same quadrant at z < 0), "none". Trim UVs:
    s = x (anchored), p = distance round the loop scaled to `metres`."""
    n = len(loop)
    lens = [0.0]
    for i in range(n):
        (za, ya), (zb, yb) = loop[i], loop[(i + 1) % n]
        lens.append(lens[-1] + math.hypot(zb - za, yb - ya))
    total = lens[-1] or 1.0

    def xs(z, end):
        mode = ends[end]
        if end == 0:
            return x0 + z if mode == "miter" else (x0 - z if mode == "outer" else x0)
        return x1 - z if mode == "miter" else (x1 + z if mode == "outer" else x1)

    for i in range(n):
        if i in hide:
            continue
        (za, ya), (zb, yb) = loop[i], loop[(i + 1) % n]
        pa, pb = lens[i] / total * metres, lens[i + 1] / total * metres
        ax0, bx0, ax1, bx1 = xs(za, 0), xs(zb, 0), xs(za, 1), xs(zb, 1)
        pts = [(ax0, ya, za), (ax1, ya, za), (bx1, yb, zb), (bx0, yb, zb)]
        add_face(faces, pts, tag, [(ax0, pa), (ax1, pa), (bx1, pb), (bx0, pb)], (0.0, -(zb - za), yb - ya), anchor=True)
    ymin = min(y for _, y in loop)
    height = (max(y for _, y in loop) - ymin) or 1.0
    for end, sgn in ((0, -1.0), (1, 1.0)):
        if ends[end] != "cap":
            continue
        x = x0 if end == 0 else x1
        pts = [(x, y, z) for z, y in loop]
        add_face(faces, pts, cap_tag or tag, [(z, (y - ymin) / height * metres) for z, y in loop], (sgn, 0.0, 0.0))


def _outward(pts, centre):
    """The face normal of `pts` pointing away from `centre` (a convex body)."""
    n = geom.newell(pts)
    m = [sum(p[k] for p in pts) / len(pts) for k in range(3)]
    d = geom.sub(m, centre)
    return n if geom.dot(n, d) >= 0 else (-n[0], -n[1], -n[2])


def box_faces(faces, lo, hi, tag_fn, skip=()) -> None:
    """An axis-aligned box with per-side tags (`tag_fn(side) -> (tag, uv
    function or None)`), sides in `skip` left out."""
    def tag(side):
        return tag_fn(side)[0]

    def uv(side, pts):
        fn = tag_fn(side)[1]
        return fn(side, pts) if fn else None

    geom.box(faces, lo, hi, tag, skip=skip, uv_fn=uv)


def shell_box(lo, hi, skip=()) -> list:
    """A camera-collider box (polygons in the piece's frame)."""
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    quads = {
        "+x": [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)],
        "-x": [(x0, y0, z1), (x0, y1, z1), (x0, y1, z0), (x0, y0, z0)],
        "+y": [(x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0)],
        "-y": [(x0, y0, z1), (x0, y0, z0), (x1, y0, z0), (x1, y0, z1)],
        "+z": [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
        "-z": [(x1, y0, z0), (x0, y0, z0), (x0, y1, z0), (x1, y1, z0)],
    }
    return [q for s, q in quads.items() if s not in skip]


# -- the gallery edge (kit_gallery_edge) ---------------------------------------------
#
# Frame: x along the railing run (0..length), y = 0 at the gallery floor, z
# into the walkable side (the gallery or landing): the void is at z < 0 and
# the slab (0.4 m, the Planner's fascia at z = 0 and its underside at
# y = -0.4) at z > 0. The balustrade stands on the floor at z 0..0.25 (the
# intrusion limit, as kit_balustrade_gothic); the dressing hangs in front of
# the fascia and under the slab's edge, at most 0.22 m into the void.
#
# Dressing variants by how far below the floor they reach (rules.py picks
# the deepest that keeps 2.4 m over any walkable surface beneath):
SLAB = 0.4  # the Planner's gallery and landing slabs (fascia 0.4 m)
EDGE_DROPS = {"full": 1.06, "plain": 0.72, "band": 0.46, "none": 0.0}
EDGE_VARIANTS = ("full", "plain", "band", "none")
EDGE_PROUD = 0.22  # the dressing's reach into the void
# Closed profiles (z, y), counter-clockwise; segments 1 and 2 lie on the
# slab's underside and on the fascia and are not drawn.
BAND_LOOP = [(0.10, -0.46), (0.10, -0.40), (0.0, -0.40), (0.0, 0.0), (-0.12, 0.0), (-0.12, -0.05), (-0.08, -0.09),
             (-0.08, -0.30), (-0.14, -0.36), (-0.14, -0.46)]
PLAIN_LOOP = [(0.10, -0.72), (0.10, -0.40), (0.0, -0.40), (0.0, 0.0), (-0.13, 0.0), (-0.13, -0.05), (-0.09, -0.09),
              (-0.09, -0.30), (-0.16, -0.36), (-0.22, -0.42), (-0.22, -0.50), (-0.12, -0.62), (-0.05, -0.72)]
EDGE_HIDE = (1, 2)
CORBEL_PITCH = 1.25  # the corbel table's rhythm
CORBEL_TOP = -0.72  # corbels hang under the cornice's foot ...
CORBEL_PROFILE = [(0.0, 0.0), (-0.17, 0.0), (-0.17, -0.08), (-0.10, -0.20), (-0.06, -0.32), (0.0, -0.32)]  # ... to 1.04 m


def _corbel_bracket(faces, xc: float, top: float = CORBEL_TOP, hw: float = 0.11) -> None:
    """One corbel of the corbel table: a moulded bracket under the cornice,
    its back on the fascia line (z = 0), 0.17 m into the void."""
    prof = [(z, top + y) for z, y in CORBEL_PROFILE]
    centre = (xc, top - 0.15, -0.06)
    bot = top + CORBEL_PROFILE[-1][1]
    # Front and underside (segments 0..4); the back (5) faces the slab line.
    lens = geom.profile_lengths(prof[:5])
    tot = lens[-1] or 1.0
    for i in range(4):
        (za, ya), (zb, yb) = prof[i], prof[i + 1]
        if abs(za - zb) < 1e-9 and abs(ya - yb) < 1e-9:
            continue
        pts = [(xc - hw, ya, za), (xc + hw, ya, za), (xc + hw, yb, zb), (xc - hw, yb, zb)]
        b0, b1 = 1.0 - lens[i] / tot, 1.0 - lens[i + 1] / tot
        add_face(faces, pts, fit("corbel_front"), [(0.0, b0), (1.0, b0), (1.0, b1), (0.0, b1)], _outward(pts, centre))
    for sx in (-1.0, 1.0):
        pts = [(xc + sx * hw, y, z) for z, y in prof]
        add_face(faces, pts, fit("corbel_side"), [(-z / 0.17, (y - bot) / (top - bot)) for z, y in prof], (sx, 0.0, 0.0))


def gallery_edge(length: float, ends=("cap", "cap"), inset=(0.225, 0.225), drops=None,
                 rail: float = 0.25) -> Piece:
    """`kit_gallery_edge` (rules, a sweep along a railing run): the
    balustrade between the newels (`inset` from each end), and the dressing
    in `drops` segments [x0, x1, variant] (grid-free: rules.py measures the
    clearance below), `ends` the dressing's corner modes at x = 0 and at
    `length`. LOD1: the balustrade's painted panel, the cornice and band, no
    corbels. A coarse camera shell over the balustrade (front, back, top)."""
    p = Piece("")
    lod: list = []
    drops = drops if drops is not None else [[0.0, length, "plain"]]
    a, b = float(inset[0]), float(inset[1])
    inner = length - a - b
    if inner > 0.3:
        bal = stairs.balustrade(inner, rail=rail)
        for src, dst in ((bal.faces, p.faces), (bal.lod1 or [], lod)):
            for f in src:
                dst.append(geom.KFace([(x + a, y, z) for x, y, z in f.pts], f.tag, f.uv, f.anchor))
    deepest = 0.0
    for x0, x1, var in drops:
        x0, x1 = float(x0), float(x1)
        if var not in EDGE_DROPS:
            raise ValueError(f"kit_gallery_edge: unknown dressing {var!r}")
        if var == "none" or x1 - x0 < 0.05:
            continue
        e0 = ends[0] if abs(x0) < 1e-6 else "cap"
        e1 = ends[1] if abs(x1 - length) < 1e-6 else "cap"
        dressing: list = []
        if var == "band":
            loop_sweep(dressing, BAND_LOOP, x0, x1, trim("string"), 0.3, (e0, e1), EDGE_HIDE)
        else:
            loop_sweep(dressing, PLAIN_LOOP, x0, x1, trim("cornice"), 0.7, (e0, e1), EDGE_HIDE)
        p.faces.extend(dressing)
        lod.extend(dressing)
        if var == "full":
            span = x1 - x0 - 0.8
            if span > 0:
                k = max(1, round(span / CORBEL_PITCH) + 1)
                step = span / (k - 1) if k > 1 else 0.0
                for i in range(k):
                    xc = x0 + 0.4 + (i * step if k > 1 else span / 2)
                    _corbel_bracket(p.faces, xc)
        deepest = max(deepest, EDGE_DROPS[var])
    p.lod1 = lod
    top = 1.0
    p.meta["collider"] = [
        [(0.0, 0.0, rail), (length, 0.0, rail), (length, top, rail), (0.0, top, rail)],
        [(length, -0.4, -0.05), (0.0, -0.4, -0.05), (0.0, top, -0.05), (length, top, -0.05)],
        [(0.0, top, rail), (length, top, rail), (length, top, -0.05), (0.0, top, -0.05)],
    ] if length > 0.3 else []
    p.meta.update(rail=rail, drop=deepest, height=top)
    return p


# -- corbelled landings (kit_landing_corbel_<d>x<w>) ----------------------------------------
#
# Frame: origin at the middle of the supporting gallery edge, at the landing's
# floor; +z into the void (the landing runs `depth` metres that way), x across
# its `width`. Two stepped corbel brackets carry it, from under the gallery's
# slab edge (z = -0.3) out toward the far edge, each course a step shorter;
# `drop` caps how far below the floor they reach (rules.py lowers it where
# the landing bridges a flight: 2.4 m stays clear over the flight).
COURSE_H = 0.36
LANDING_COURSES = 6


def landing_corbel(depth: float, width: float, drop: float = 2.6) -> Piece:
    """`kit_landing_corbel_4.5x4` (the x-crossings' landings) and
    `kit_landing_corbel_3.5x4` (the z-crossings' gallery landings)."""
    p = Piece("")
    n = max(1, min(LANDING_COURSES, int((drop - SLAB + 1e-6) // COURSE_H)))
    hw = 0.32
    back = -0.3
    shells = []
    for xc in (-(width / 2 - 0.5), width / 2 - 0.5):
        reach = [depth - 0.3 if n == 1 else 0.7 + (depth - 1.0) * (1 - k / (n - 1)) for k in range(n)]
        for k in range(n):
            y1 = -SLAB - COURSE_H * k
            y0 = y1 - COURSE_H
            zf = reach[k]
            x0, x1 = xc - hw, xc + hw
            # Front, with its own corbel moulding (fit tile), and the sides.
            add_face(p.faces, [(x0, y0, zf), (x1, y0, zf), (x1, y1, zf), (x0, y1, zf)], fit("corbel_front"),
                     [(0, 0), (1, 0), (1, 1), (0, 1)], (0.0, 0.0, 1.0))
            for x, sx in ((x0, -1.0), (x1, 1.0)):
                add_face(p.faces, [(x, y0, back), (x, y0, zf), (x, y1, zf), (x, y1, back)], fit("corbel_side"),
                         [(0, 0), (1, 0), (1, 1), (0, 1)], (sx, 0.0, 0.0))
            add_face(p.faces, [(x0, y0, back), (x1, y0, back), (x1, y1, back), (x0, y1, back)], fit("corbel_side"),
                     [(0, 0), (1, 0), (1, 1), (0, 1)], (0.0, 0.0, -1.0))
            # The underside: the step this course shows below the next one
            # (the last course its whole bottom).
            zb = reach[k + 1] if k + 1 < n else back
            add_face(p.faces, [(x0, y0, zb), (x1, y0, zb), (x1, y0, zf), (x0, y0, zf)], CEILING, None, (0.0, -1.0, 0.0))
        bottom = -SLAB - COURSE_H * n
        shells += shell_box((xc - hw, bottom, back), (xc + hw, -SLAB, reach[0]), skip=("+y",))
    p.meta.update(collider=shells, drop=SLAB + COURSE_H * n, courses=n)
    return p


# -- the pier stack (kit_pier_stack_<s>) -----------------------------------------------------
#
# Frame: the pier's foot centre (y = 0 the ground floor). The Planner draws
# the solid pier (the ground floor's cut-out) up to the first pier landing;
# this piece dresses it (plinth, string courses, a capital cornice on its
# void faces) and stands four slender corner colonnettes on every pier
# landing up to the next one's slab, so no landing floats. Pier landings
# are 2 storeys apart; `top` is the highest one's floor.
COLONNETTE = 0.45  # base and capital square; the shaft 0.32
COLONNETTE_OUT = 0.025  # its centre this far outside the landing's corner: 0.2 m inside the landing (<= 0.25)
FLIGHT_HALF = 2.0  # the 4 m flights through the pier landings (no colonnette or cornice crosses them)


def pier_landings(top: float, first: float = G.STOREY) -> list[float]:
    """Pier landing floors from `top` down two storeys at a time, above `first`."""
    out = []
    y = top
    while y > first + 1e-6:
        out.append(y)
        y -= 2 * G.STOREY
    return sorted(out)


def colonnette_spots(size: float):
    """(x, z) centres of the four corner colonnettes (pier frame)."""
    h = size / 2 + COLONNETTE_OUT
    return [(sx * h, sz * h) for sx in (-1.0, 1.0) for sz in (-1.0, 1.0)]


def _colonnette(faces, cx: float, cz: float, y0: float, y1: float) -> list:
    hb, hs = COLONNETTE / 2, 0.16
    band = trim("capital_band")

    def bands(side, pts):
        return [((pt[0] - cx) + (pt[2] - cz), (pt[1] - y0) % 0.3) for pt in pts]

    def shaft_uv(side, pts):
        return [(pt[1], ((pt[0] - cx) + (pt[2] - cz) + 2 * hs) / (4 * hs) * 0.7) for pt in pts]

    box_faces(faces, (cx - hb, y0, cz - hb), (cx + hb, y0 + 0.4, cz + hb), lambda s: (band, bands), skip=("-y",))
    mid0 = (y0 + y1) / 2 - 0.1
    box_faces(faces, (cx - hs, y0 + 0.4, cz - hs), (cx + hs, mid0, cz + hs), lambda s: (trim("shaft"), shaft_uv),
              skip=("-y", "+y"))
    box_faces(faces, (cx - 0.2, mid0, cz - 0.2), (cx + 0.2, mid0 + 0.2, cz + 0.2), lambda s: (band, bands))
    box_faces(faces, (cx - hs, mid0 + 0.2, cz - hs), (cx + hs, y1 - 0.3, cz + hs), lambda s: (trim("shaft"), shaft_uv),
              skip=("-y", "+y"))
    box_faces(faces, (cx - hb, y1 - 0.3, cz - hb), (cx + hb, y1, cz + hb), lambda s: (band, bands), skip=("+y",))
    return shell_box((cx - hb, y0, cz - hb), (cx + hb, y1, cz + hb), skip=("+y", "-y"))


def pier_stack(size: float, top: float = 49.5) -> Piece:
    """`kit_pier_stack_4.5`."""
    p = Piece("")
    h = size / 2
    lands = pier_landings(top)
    first = lands[0]
    f = p.faces
    # String courses round the solid pier (outside corners); its plinth is
    # the rules' (the pier's faces are wall runs of the ground floor).
    for y, prof, strip, metres in ((4.5, mouldings.STRING_PROFILE, "string", 0.3),
                                   (9.0, mouldings.STRING_PROFILE, "string", 0.3)):
        for yaw in (0.0, math.pi / 2, math.pi, -math.pi / 2):
            body = [(z, yy + y) for z, yy in prof]
            tmp: list = []
            geom.sweep(tmp, body, -h, h, trim(strip), metres, ends=("outer", "outer"))
            for q in tmp:
                f.append(geom.KFace([geom.rot_y(pt, yaw) for pt in _push(q.pts, h)], q.tag, q.uv, q.anchor))
    # The capital cornice under the first pier landing, on the void faces
    # (east and west) with short returns beside the flights.
    cap = [(z, yy + first) for z, yy in mouldings.CORNICE_PROFILE]
    for yaw in (math.pi / 2, -math.pi / 2):
        tmp = []
        geom.sweep(tmp, cap, -h, h, trim("cornice"), 0.7, ends=("outer", "outer"))
        for q in tmp[: len(cap) - 2] + tmp[len(cap) - 1:]:
            f.append(geom.KFace([geom.rot_y(pt, yaw) for pt in _push(q.pts, h)], q.tag, q.uv, q.anchor))
    for yaw in (0.0, math.pi):
        for x0, x1 in ((-h, -FLIGHT_HALF), (FLIGHT_HALF, h)):
            tmp = []
            ends = ("outer", "cap") if x0 < 0 else ("cap", "outer")
            geom.sweep(tmp, cap, x0, x1, trim("cornice"), 0.7, ends=ends)
            for q in tmp:
                if abs(geom.newell(q.pts)[1]) > 0.5 * geom.norm(geom.newell(q.pts)) and min(pt[1] for pt in q.pts) > first - 1e-6:
                    continue  # its top lies on the landing's floor
                f.append(geom.KFace([geom.rot_y(pt, yaw) for pt in _push(q.pts, h)], q.tag, q.uv, q.anchor))
    # Corner colonnettes from each pier landing to the next one's slab.
    shells = []
    for y0, y1 in zip(lands, lands[1:]):
        for cx, cz in colonnette_spots(size):
            shells += _colonnette(f, cx, cz, y0, y1 - SLAB)
    p.meta.update(collider=shells, landings=lands, colonnettes=colonnette_spots(size))
    return p


def _push(pts, h):
    """A face swept along a wall face at z = 0 moved out to the pier's face (z = h)."""
    return [(x, y, z + h) for x, y, z in pts]


# -- hanging flights with stepped undersides (kit_stair_flight_stepped) ---------------------------
#
# The Grand Staircase's flights that do not rest on the floor: the
# treads, nosings and risers of kit_stair_flight (castle_kit/pieces/stairs.py,
# its meta["treads"] for the lint), stringer sides stepping with the treads
# and a stepped underside: each step's block has its own flat bottom
# (castle_ceiling) and a riser-like face up to the next one (castle_wall),
# as a stone stair seen from below. Frame as kit_stair_flight.
STEP_BLOCK = 0.36  # each step's block reaches this far below its tread top
SIDE_DROP = 0.01  # sides stop under the tread's edge: a balustrade's base on the stair's edge starts where the tread's top does


def flight_stepped(width: float, length: float, rise: float, foot: bool = True, crown: bool = True) -> Piece:
    """`foot` False: the flight's foot stands against a wall (the solid
    pier), which shows there instead of the lower slab's edge; `crown` False:
    its top half riser would lie on a wall face (it meets the pier's face),
    so it is left out."""
    p = stairs.flight(width, length, rise, solid=True)
    n = p.meta["steps"]
    if not crown:
        p.faces = [q for q in p.faces if not (all(abs(pt[2] - length) < 1e-9 for pt in q.pts)
                                              and min(pt[1] for pt in q.pts) > rise - rise / n - 1e-6)]
    d, r = length / n, rise / n
    f = p.faces
    for i in range(n):
        a, b = i * d, (i + 1) * d
        top = (i + 0.5) * r
        bot = top - STEP_BLOCK
        # The block's bottom and, up the flight, the face up to the next block's bottom.
        add_face(f, [(0.0, bot, a), (width, bot, a), (width, bot, b), (0.0, bot, b)], CEILING, None, (0.0, -1.0, 0.0))
        # (The last block's end lies against the landing's slab.)
        if i + 1 < n:
            add_face(f, [(0.0, bot, b), (width, bot, b), (width, bot + r, b), (0.0, bot + r, b)], WALL, None,
                     (0.0, 0.0, 1.0))
        if i == 0 and bot > -SLAB and foot:
            # The lower gallery's slab edge under the flight's foot (the
            # Planner draws no fascia across a walkable join).
            add_face(f, [(0.0, -SLAB, a), (width, -SLAB, a), (width, bot, a), (0.0, bot, a)], WALL, None, (0.0, 0.0, 1.0))
        for x, sx in ((0.0, -1.0), (width, 1.0)):
            # Sides stop at the tread's back edge: the nosing's 3 cm overhang
            # would overlap the next step's side on the same plane.
            pts = [(x, bot, a), (x, bot, b), (x, top - SIDE_DROP, b), (x, top - SIDE_DROP, a)]
            add_face(f, pts, WALL, None, (sx, 0.0, 0.0))
    p.meta.update(stepped=True, block=STEP_BLOCK)
    return p


# -- the skylight lantern (kit_skylight_lantern) ---------------------------------------------
#
# Frame: its top centre at the ceiling (y = 0; everything hangs below), x
# across `w`, z along `d`. A moulded frame ring just under the ceiling (no
# face on the ceiling's plane), glazed sides between mullions, a hipped
# glazed bottom with its ridge along z and a pendant boss: the glass is
# castle_glass (sky-tinted), the frame castle_trim.
SKY_RING = 0.6
SKY_SIDE = 2.4
SKY_HIP = 1.3
SKY_GAP = 0.02  # the ring's top stays this far under the ceiling


def skylight_lantern(w: float = 12.0, d: float = 16.0) -> Piece:
    p = Piece("")
    f = p.faces
    hw, hd = w / 2, d / 2
    iw, idp = hw - SKY_RING, hd - SKY_RING
    y_ring = -SKY_GAP - 0.55
    centre = (0.0, -2.0, 0.0)
    # The ring: outer sides, its underside between the outer edge and the glass box.
    outer = [(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)]
    inner = [(-iw, -idp), (iw, -idp), (iw, idp), (-iw, idp)]
    for i in range(4):
        (xa, za), (xb, zb) = outer[i], outer[(i + 1) % 4]
        pts = [(xa, y_ring, za), (xb, y_ring, zb), (xb, -SKY_GAP, zb), (xa, -SKY_GAP, za)]
        length = math.hypot(xb - xa, zb - za)
        add_face(f, pts, trim("cornice"), [(0.0, 0.0), (length, 0.0), (length, 0.7), (0.0, 0.7)], _outward(pts, centre),
                 anchor=False)
        (xc, zc), (xd, zd) = inner[i], inner[(i + 1) % 4]
        pts = [(xa, y_ring, za), (xc, y_ring, zc), (xd, y_ring, zd), (xb, y_ring, zb)]
        add_face(f, pts, trim("capital_band"), [(0.0, 0.0), (0.0, 0.3), (length, 0.3), (length, 0.0)], (0.0, -1.0, 0.0))
    # Glazed sides with mullions (boxes 0.14 m square) and a transom.
    y0, y1 = y_ring - SKY_SIDE, y_ring
    for i in range(4):
        (xa, za), (xb, zb) = inner[i], inner[(i + 1) % 4]
        length = math.hypot(xb - xa, zb - za)
        panes = max(2, round(length / 2.0))
        ux, uz = (xb - xa) / length, (zb - za) / length
        nx, nz = uz, -ux  # outward for this winding
        for k in range(panes):
            s0, s1 = k * length / panes, (k + 1) * length / panes
            pa = (xa + ux * s0, za + uz * s0)
            pb = (xa + ux * s1, za + uz * s1)
            for yy0, yy1 in ((y0, (y0 + y1) / 2), ((y0 + y1) / 2, y1)):
                pts = [(pa[0], yy0, pa[1]), (pb[0], yy0, pb[1]), (pb[0], yy1, pb[1]), (pa[0], yy1, pa[1])]
                add_face(f, pts, GLASS, [(0, 0), (1, 0), (1, 1), (0, 1)], (nx, 0.0, nz))
        for k in range(panes):  # (the corner mullion belongs to the side that starts there)
            s = k * length / panes
            mx, mz = xa + ux * s, za + uz * s
            lo = (mx - 0.07 + nx * 0.0, y0, mz - 0.07)
            hi = (mx + 0.07, y1, mz + 0.07)
            box_faces(f, (min(lo[0], hi[0]), y0, min(lo[2], hi[2])), (max(lo[0], hi[0]), y1, max(lo[2], hi[2])),
                      lambda s_: (trim("window_frame"), lambda side, pts: [(pt[1], 0.15) for pt in pts]),
                      skip=("+y", "-y"))
        # The transom: a frame band round the middle.
        ym = (y0 + y1) / 2
        o = 0.09  # in front of the mullions' faces (0.07)
        pts = [(xa + nx * o, ym - 0.07, za + nz * o), (xb + nx * o, ym - 0.07, zb + nz * o),
               (xb + nx * o, ym + 0.07, zb + nz * o), (xa + nx * o, ym + 0.07, za + nz * o)]
        add_face(f, pts, trim("window_frame"), [(0.0, 0.0), (length, 0.0), (length, 0.3), (0.0, 0.3)], (nx, 0.0, nz),
                 anchor=False)
    # The hipped glazed bottom: ridge along z at y0 - SKY_HIP.
    yr = y0 - SKY_HIP
    rz = idp - iw
    ridge = [(0.0, yr, -rz), (0.0, yr, rz)]
    hip_faces = [
        [(-iw, y0, -idp), (iw, y0, -idp), ridge[0]],  # north end
        [(iw, y0, idp), (-iw, y0, idp), ridge[1]],  # south end
        [(iw, y0, -idp), (iw, y0, idp), ridge[1], ridge[0]],  # east
        [(-iw, y0, idp), (-iw, y0, -idp), ridge[0], ridge[1]],  # west
    ]
    for pts in hip_faces:
        uv = geom.bbox_uv([(pt[0] + pt[2], pt[1]) for pt in pts])
        add_face(f, pts, GLASS, uv, _outward(pts, (0.0, y0 + 0.5, 0.0)))
    # Hip and ridge bars, and a pendant boss under the ridge's middle.
    for a, b in (((-iw, y0, -idp), ridge[0]), ((iw, y0, -idp), ridge[0]), ((iw, y0, idp), ridge[1]),
                 ((-iw, y0, idp), ridge[1]), (ridge[0], ridge[1])):
        _bar(f, a, b, 0.08)
    for r0, ya, r1, yb in ((0.16, yr, 0.22, yr - 0.25), (0.22, yr - 0.25, 0.0, yr - 0.6)):
        _octa(f, r0, ya, r1, yb)
    p.meta["collider"] = shell_box((-hw, yr - 0.6, -hd), (hw, -SKY_GAP, hd), skip=("+y",))
    p.meta.update(bottom=yr - 0.6)
    return p


def _bar(faces, a, b, t: float) -> None:
    """A square bar from a to b (castle_trim window_frame), its four sides."""
    ax = geom.unit(geom.sub(b, a))
    up = (0.0, 1.0, 0.0) if abs(ax[1]) < 0.9 else (1.0, 0.0, 0.0)
    u = geom.unit(geom.cross(ax, up))
    v = geom.unit(geom.cross(u, ax))
    length = geom.norm(geom.sub(b, a))
    corners = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    for i in range(4):
        (s0, t0), (s1, t1) = corners[i], corners[(i + 1) % 4]
        o0 = (u[0] * s0 * t / 2 + v[0] * t0 * t / 2, u[1] * s0 * t / 2 + v[1] * t0 * t / 2, u[2] * s0 * t / 2 + v[2] * t0 * t / 2)
        o1 = (u[0] * s1 * t / 2 + v[0] * t1 * t / 2, u[1] * s1 * t / 2 + v[1] * t1 * t / 2, u[2] * s1 * t / 2 + v[2] * t1 * t / 2)
        pts = [geom.add(a, o0), geom.add(a, o1), geom.add(b, o1), geom.add(b, o0)]
        mid = geom.add(geom.add(o0, o1), (0.0, 0.0, 0.0))
        add_face(faces, pts, trim("window_frame"), [(0.0, 0.0), (0.0, 0.3), (length, 0.3), (length, 0.0)], mid)


def _octa(faces, r0, y0, r1, y1, n: int = 8) -> None:
    pts0 = [(r0 * math.cos(2 * math.pi * (i + 0.5) / n), r0 * math.sin(2 * math.pi * (i + 0.5) / n)) for i in range(n)]
    pts1 = [(r1 * math.cos(2 * math.pi * (i + 0.5) / n), r1 * math.sin(2 * math.pi * (i + 0.5) / n)) for i in range(n)]
    for i in range(n):
        j = (i + 1) % n
        q = [(pts0[i][0], y0, pts0[i][1]), (pts0[j][0], y0, pts0[j][1]), (pts1[j][0], y1, pts1[j][1]), (pts1[i][0], y1, pts1[i][1])]
        mx, mz = (pts0[i][0] + pts0[j][0]) / 2, (pts0[i][1] + pts0[j][1]) / 2
        add_face(faces, q, fit("boss"), [(i / n, 1.0), ((i + 1) / n, 1.0), ((i + 1) / n, 0.0), (i / n, 0.0)], (mx, 0.0, mz))


# -- the floor compass (kit_floor_compass_12) -------------------------------------------------
#
# Frame: its centre on the floor, round the pier's foot. A flush inlay: the
# floor under its square is cut (rules.py and assemble.py, meta
# "floor_cut"), and the piece draws the whole square again: plain floor
# (castle_floor, world UVs, seamless with the rest) between the pier and
# the ring and outside the ring, the ring in castle_props palette names
# (brass rims, light and dark stone wedges, brass cardinal wedges).
COMPASS_SEGMENTS = 32


def _on_square(theta: float, half: float) -> tuple[float, float]:
    c, s = math.cos(theta), math.sin(theta)
    t = half / max(abs(c), abs(s))
    return (c * t, s * t)


def floor_compass(size: float, inner: float = 3.4, pier: float = 4.5) -> Piece:
    """`kit_floor_compass_12`: the ring from `inner` to size / 2."""
    p = Piece("")
    f = p.faces
    n = COMPASS_SEGMENTS
    outer = size / 2
    rings = [(inner, inner + 0.2, BRASS), (inner + 0.2, outer - 0.4, None), (outer - 0.4, outer - 0.2, BRASS),
             (outer - 0.2, outer, COMPASS_DARK)]
    th = [2 * math.pi * i / n for i in range(n + 1)]

    def circ(r, t):
        return (r * math.cos(t), 0.0, r * math.sin(t))

    def quad(pts, tag):
        uv = [(pt[0], -pt[2]) for pt in pts] if tag != FLOOR else None
        add_face(f, pts, tag, uv, (0.0, 1.0, 0.0))

    for i in range(n):
        a, b = th[i], th[i + 1]
        sa, sb = _on_square(a, pier / 2), _on_square(b, pier / 2)
        quad([(sa[0], 0.0, sa[1]), circ(inner, a), circ(inner, b), (sb[0], 0.0, sb[1])], FLOOR)
        wedge = ((i + 1) // 2) % 16  # 16 wedges of two segments; wedge 0 straddles +x (segments 31 and 0)
        cardinal = (wedge % 4) == 0
        for r0, r1, tag in rings:
            if tag is None:
                tag = BRASS if cardinal else (COMPASS_LIGHT if wedge % 2 else COMPASS_DARK)
            quad([circ(r0, a), circ(r1, a), circ(r1, b), circ(r0, b)], tag)
        oa, ob = _on_square(a, outer), _on_square(b, outer)
        quad([circ(outer, a), (oa[0], 0.0, oa[1]), (ob[0], 0.0, ob[1]), circ(outer, b)], FLOOR)
    p.meta.update(floor_cut=[(-outer, -outer, outer, outer)], inner=inner, outer=outer, pier=pier)
    return p
