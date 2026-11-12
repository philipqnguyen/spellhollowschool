"""Castle M5a (Tower): the west tower's pieces (docs/art/castle-kit.md "Pieces").

- `kit_spiral_core_<s>`: the arcaded newel core of the square spiral, an
  `s` x `s` column (the Planner draws its four walls) dressed with corner
  rolls, a cornice ring under the shaft's ceiling and a blind arcade on
  every stretch of floor or flight that runs along it: level pointed arches
  on paired colonnettes, stepping up with the flights. Its camera collider
  shell is a box just outside the relief.
- `kit_window_slit_<w>x<h>`: a slit window through the shell: a pointed
  opening on the inside face whose reveals splay in to a narrow glazed
  light, then run straight out to the outer face.
- `kit_door_knocker_a`: the closed oak pair in common room C's 4 x 4.5
  arch with a bronze bird knocker, our own design (a long-necked wading
  bird, wings folded, standing on a bar, a ring hanging from its beak; no
  borrowed crest or creature).

Frames as in geom.py (x along the wall to the right of someone facing it,
y up, z out of the wall face into the room). The core's frame is the
column's foot centre (y = 0 at `base`), its x and z the world's (yaw 0).
Pure Python; colours only through face tags (trim strips and tiles, the
props atlas's palette names): no colour literal here
(docs/art/castle-kit.md "Colours come from palette data only", "Textures").
"""

from __future__ import annotations

import math

from castle_kit import grid as G
from castle_kit.pieces import geom, mouldings, openings
from castle_kit.pieces.geom import Piece, add_face

WALL = "castle_wall|world"
GLASS = "castle_glass|fit:light_lancet"
BRONZE = G.palette_tag("metal", "bronze")
# The props-atlas slots these pieces use (castle_textures.json props_slots).
SLOTS = (("metal", "@bronze"),)


def trim(strip: str) -> str:
    return f"castle_trim|trim:{strip}"


def fit(tile: str) -> str:
    return f"castle_trim|fit:{tile}"


def _place(faces, out, yaw: float = 0.0, shift=(0.0, 0.0, 0.0)) -> None:
    """Faces built in a local frame, turned by `yaw` and moved by `shift`."""
    for f in faces:
        pts = [geom.add(geom.rot_y(p, yaw), shift) for p in f.pts]
        out.append(geom.KFace(pts, f.tag, f.uv, f.anchor))


def _box(faces, lo, hi, tag, skip=(), metres: float = 0.3) -> None:
    """A trim-strip box: s along x (or y on the sides), p across."""
    def uv(side, pts):
        if side in ("+x", "-x"):
            return [(pt[2], (pt[1] - lo[1]) % metres) for pt in pts]
        return [(pt[0], ((pt[1] if side in ("+z", "-z") else pt[2]) - (lo[1] if side in ("+z", "-z") else lo[2]))
                 % metres) for pt in pts]
    geom.box(faces, lo, hi, tag, skip=skip, uv_fn=uv)


# -- the spiral core (kit_spiral_core_<s>) ---------------------------------------------------
#
# Arcade numbers: arches about ARCADE_PITCH wide, springing ARCADE_SPRING
# above the higher floor under each (so the arches step up with a flight),
# colonnettes and rims at most CORE_RELIEF proud (the intrusion limit is
# 0.25 m below 2.4 m; the camera keeps 0.3 m off walls).
ARCADE_PITCH = 2.0
ARCADE_SPRING = 2.4
ARCADE_SEG = 4  # arc segments per side of an arch
COLONNETTE_W = 0.18
COLONNETTE_PROUD = 0.12
CAP_H, CAP_W, CAP_PROUD = 0.18, 0.28, 0.16  # capitals and bases
BASE_H = 0.24
RIM_W, RIM_PROUD = 0.2, 0.12  # the arch's roll ...
HOOD_W, HOOD_PROUD = 0.09, 0.16  # ... and its hood mould
SKIRT_H, SKIRT_PROUD = 0.45, 0.1  # a raking plinth along each flight (flat floors keep the rules' plinth)
SKIRT_SINK = 0.12  # its foot below the flight's ramp (the treads stand up to half a riser off it)
SKIRT_PROFILE = [(0.0, 0.0), (SKIRT_PROUD, 0.0), (SKIRT_PROUD, 0.34), (0.06, 0.41), (0.0, SKIRT_H)]
CORNER_ROLL = 0.16  # the corner rolls stand this far off both faces
CORNER_CLEAR = 0.22  # arcades keep this from a corner's line
CORE_RELIEF = max(CAP_PROUD, HOOD_PROUD, CORNER_ROLL, SKIRT_PROUD)
SHELL_GAP = 0.01  # the collider shell this far outside the relief
# Face yaws: local +z out of the face (south 0, east pi/2, north pi, west -pi/2).
FACE_YAW = {"s": 0.0, "e": math.pi / 2, "n": math.pi, "w": -math.pi / 2}


def core_face(side: str, half: float, x: float, z: float) -> float:
    """World-aligned core frame (x, z) -> the face-local u of `side` (x along
    the face to the right of someone facing it)."""
    if side == "s":
        return x
    if side == "n":
        return -x
    if side == "e":
        return -z
    return z


def _arch(faces, u0: float, u1: float, f0: float, f1: float, lift: float = 0.0) -> None:
    """One blind arch over u0..u1 (face-local, z = 0 the face), the floor
    rising linearly from f0 at u0 to f1 at u1: two colonnettes standing
    `lift` over the floor (on its plinth), level springing ARCADE_SPRING
    over the higher end, a roll and a hood mould round the head."""
    cw = COLONNETTE_W
    off = max(CAP_W / 2, RIM_W / 2 + HOOD_W) + 0.02  # (paired bases and hoods of neighbouring arches stay apart)
    ca, cb = u0 + off, u1 - off
    span = cb - ca
    spring = max(f0, f1) + ARCADE_SPRING + lift

    def floor(u):
        return f0 + (f1 - f0) * (u - u0) / ((u1 - u0) or 1.0)

    for uc in (ca, cb):
        fy = floor(uc) + lift - 0.05  # (sunk into the plinth: bases never float)
        _box(faces, (uc - CAP_W / 2, fy, 0.0), (uc + CAP_W / 2, fy + BASE_H, CAP_PROUD), trim("capital_band"), skip=("-z", "-y"))
        geom.box(faces, (uc - cw / 2, fy + BASE_H, 0.0), (uc + cw / 2, spring - CAP_H, COLONNETTE_PROUD), trim("shaft"),
                 skip=("-z", "-y", "+y"),
                 uv_fn=lambda side, pts, uc=uc: [(pt[1], ((pt[0] - uc + cw / 2) + pt[2]) / (cw + 2 * COLONNETTE_PROUD) * 0.7)
                                                 for pt in pts])
        _box(faces, (uc - CAP_W / 2, spring - CAP_H, 0.0), (uc + CAP_W / 2, spring, CAP_PROUD), trim("capital_band"),
             skip=("-z",))
    inner = [(x + (ca + cb) / 2, y) for x, y in geom.pointed_arc(span, spring, ARCADE_SEG, grow=-RIM_W / 2)]
    outer = [(x + (ca + cb) / 2, y) for x, y in geom.pointed_arc(span, spring, ARCADE_SEG, grow=RIM_W / 2)]
    geom.polyline_band(faces, inner, outer, 0.0, RIM_PROUD, trim("arch_rim"), 0.3)
    hood = [(x + (ca + cb) / 2, y) for x, y in geom.pointed_arc(span, spring, ARCADE_SEG, grow=RIM_W / 2 + HOOD_W)]
    geom.polyline_band(faces, outer, hood, 0.0, HOOD_PROUD, trim("string"), 0.3)


def arcade(faces, a: float, b: float, fa: float, fb: float) -> int:
    """A blind arcade along a face from u = a to b over a floor from fa to
    fb (linear): equal arches about ARCADE_PITCH wide; along a flight (fa !=
    fb) a raking plinth under it, the colonnettes standing on it, on a flat
    floor on the rules' plinth. Returns the count."""
    n = max(1, int(round((b - a) / ARCADE_PITCH)))
    w = (b - a) / n
    raking = abs(fb - fa) > 1e-6
    lift = SKIRT_H - SKIRT_SINK if raking else G.PLINTH.height
    if raking:
        k = (fb - fa) / (b - a)
        geom.sweep(faces, [(zz, yy + fa - SKIRT_SINK - k * a) for zz, yy in SKIRT_PROFILE], a, b, trim("plinth"), 0.6,
                   ends=("cap", "cap"), y_at=lambda x: k * x)
    for i in range(n):
        u0, u1 = a + i * w, a + (i + 1) * w
        f0 = fa + (fb - fa) * (u0 - a) / (b - a)
        f1 = fa + (fb - fa) * (u1 - a) / (b - a)
        _arch(faces, u0, u1, f0, f1, lift)
    return n


def spiral_core(size: float, top: float = 60.75, base: float = 0.0, runs=()) -> Piece:
    """`kit_spiral_core_<s>`: `top` and `base` are world heights (the frame's
    y = 0 is `base`); `runs` lists the stretches of floor along the faces as
    [side, u0, u1, y0, y1] (side "n" | "e" | "s" | "w", u face-local, y the
    floor's height above `base` at u0 and u1), from the rules."""
    p = Piece("")
    h = size / 2.0
    height = top - base
    e = CORNER_ROLL
    under = height - G.CORNICE.height
    # Corner rolls: the L of a square roll round each vertical edge.
    for sx in (-1.0, 1.0):
        for sz in (-1.0, 1.0):
            cx, cz = sx * h, sz * h
            quads = [
                ([(cx + sx * e, 0.0, cz - sz * e), (cx + sx * e, 0.0, cz + sz * e)], (sx, 0.0, 0.0)),
                ([(cx - sx * e, 0.0, cz + sz * e), (cx + sx * e, 0.0, cz + sz * e)], (0.0, 0.0, sz)),
                ([(cx, 0.0, cz - sz * e), (cx + sx * e, 0.0, cz - sz * e)], (0.0, 0.0, -sz)),
                ([(cx - sx * e, 0.0, cz), (cx - sx * e, 0.0, cz + sz * e)], (-sx, 0.0, 0.0)),
            ]
            for (pa, pb), n in quads:
                pts = [pa, pb, (pb[0], under, pb[2]), (pa[0], under, pa[2])]
                w = math.hypot(pb[0] - pa[0], pb[2] - pa[2])
                add_face(p.faces, pts, trim("shaft"), [(0.0, 0.0), (0.0, w / (2 * e) * 0.35), (under, w / (2 * e) * 0.35),
                                                       (under, 0.0)], n)
    # The cornice ring under the shaft's ceiling (outside corners mitred).
    for side, yaw in FACE_YAW.items():
        ring = mouldings.cornice(size, ends=("outer", "outer"))
        local = []
        for f in ring.faces:
            local.append(geom.KFace([(x - h, y + height, z) for x, y, z in f.pts], f.tag, f.uv, f.anchor))
        _place(local, p.faces, yaw, geom.rot_y((0.0, 0.0, h), yaw))
    # The blind arcades.
    count = 0
    for side, u0, u1, y0, y1 in runs:
        a, b = max(u0, -h + CORNER_CLEAR), min(u1, h - CORNER_CLEAR)
        if b - a < 0.8:
            continue
        fa = y0 + (y1 - y0) * (a - u0) / ((u1 - u0) or 1.0)
        fb = y0 + (y1 - y0) * (b - u0) / ((u1 - u0) or 1.0)
        local: list = []
        count += arcade(local, a, b, fa, fb)
        _place(local, p.faces, FACE_YAW[side], geom.rot_y((0.0, 0.0, h), FACE_YAW[side]))
    # The camera collider shell: four walls just outside the relief.
    r = h + CORE_RELIEF + SHELL_GAP
    shell = []
    for side, yaw in FACE_YAW.items():
        quad = [(-r, 0.0, r), (r, 0.0, r), (r, height, r), (-r, height, r)]
        shell.append([geom.rot_y(q, yaw) for q in quad])
    p.meta.update(collider=shell, arches=count, relief=CORE_RELIEF, height=height)
    return p


# -- the slit window (kit_window_slit_<w>x<h>) ------------------------------------------------
#
# Frame as openings.window: y = 0 the sill, z = 0 the inside face, the wall
# mass to z = -depth. The Planner cuts a `width` x `height` hole; the piece
# closes its pointed head on both faces, splays the reveals in to the light
# at SLIT_AT x depth and runs them straight out from there.
SLIT_SEG = 3
SLIT_LIGHT = 0.18  # the glazed light's width
SLIT_RAISE = 0.15  # its sill above the opening's
SLIT_DROP = 0.15  # its apex below the opening's
SLIT_AT = 0.55  # the glass at this share of the wall's depth
SLIT_FRAME = (0.04, 0.16, 0.05)  # the inside frame: from, to (beyond the opening's edge), proud


def slit_window(width: float, height: float, depth: float = G.EXTERIOR_SHELL, outer: bool = True) -> Piece:
    p = Piece("")
    f = p.faces
    half = width / 2.0
    spring = height - G.ARCH_RISE * width
    zg = -depth * SLIT_AT
    big = geom.lancet_outline(width, height, SLIT_SEG)
    small = [(x, y + SLIT_RAISE) for x, y in geom.lancet_outline(SLIT_LIGHT, height - SLIT_RAISE - SLIT_DROP, SLIT_SEG)]
    n = len(big)
    # The head's spandrels on the inside face (and the outer face).
    openings._spandrels(f, width, height, spring, 0.0, 1.0, SLIT_SEG)
    if outer:
        openings._spandrels(f, width, height, spring, -depth, -1.0, SLIT_SEG)
    # Splayed reveals from the opening in to the light, then straight out.
    for i in range(n):
        j = (i + 1) % n
        a0, a1 = big[i], big[j]
        b0, b1 = small[i], small[j]
        mid = ((a0[0] + a1[0] + b0[0] + b1[0]) / 4, (a0[1] + a1[1] + b0[1] + b1[1]) / 4)
        cy = height / 2.0
        inward = (-(mid[0]), cy - mid[1])  # toward the light's axis
        pts = [(a0[0], a0[1], 0.0), (a1[0], a1[1], 0.0), (b1[0], b1[1], zg), (b0[0], b0[1], zg)]
        nn = geom.newell(pts)
        if nn[0] * inward[0] + nn[1] * inward[1] < 0:
            nn = (-nn[0], -nn[1], -nn[2])
        add_face(f, pts, WALL, None, nn)
        pts = [(b0[0], b0[1], zg), (b1[0], b1[1], zg), (b1[0], b1[1], -depth), (b0[0], b0[1], -depth)]
        ex, ey = b1[0] - b0[0], b1[1] - b0[1]
        nrm = (ey, -ex, 0.0)
        if nrm[0] * inward[0] + nrm[1] * inward[1] < 0:
            nrm = (-ey, ex, 0.0)
        add_face(f, pts, WALL, None, nrm)
        if outer:
            # The outer face round the light, inside the shell's hole.
            pts = [(a0[0], a0[1], -depth), (a1[0], a1[1], -depth), (b1[0], b1[1], -depth), (b0[0], b0[1], -depth)]
            add_face(f, pts, WALL, None, (0.0, 0.0, -1.0))
    # The glass (both faces).
    uv = geom.bbox_uv(small)
    add_face(f, [(x, y, zg) for x, y in small], GLASS, uv, (0.0, 0.0, 1.0))
    add_face(f, [(x, y, zg) for x, y in reversed(small)], GLASS, [(1.0 - a, b) for a, b in reversed(uv)], (0.0, 0.0, -1.0))
    # A chamfered frame round the opening on the inside face.
    g0, g1, proud = SLIT_FRAME
    inner = [(-half - g0, 0.0)] + geom.pointed_arc(width, spring, SLIT_SEG, grow=g0) + [(half + g0, 0.0)]
    outer_c = [(-half - g1, 0.0)] + geom.pointed_arc(width, spring, SLIT_SEG, grow=g1) + [(half + g1, 0.0)]
    geom.polyline_band(f, inner, outer_c, 0.0, proud, trim("window_frame"), 0.3)
    p.meta.update(spring=spring, lights=1, glass=zg)
    return p


# -- the knocker door (kit_door_knocker_a) --------------------------------------------------------
#
# Frame: the door's centre on the wall face at its floor (z = 0 the face,
# the passage at z < 0); the leaves stand KNOCKER_INSET into the passage,
# inside the arch's 0.5 m reveal. The knocker hangs on the meeting stile.
KNOCKER_DOOR_SIZE = (4.0, 4.5)  # the cr_c_door arch (kit_stone_arch_4x4.5)
KNOCKER_INSET = 0.35
KNOCKER_Y = 1.0  # the bird's feet above the floor (its ring hangs at about 1.6 m)


def _tri_prism(faces, pts2, z0: float, z1: float, tag: str) -> None:
    """A flat plate: the outline pts2 [(x, y), ...] (convex, CCW) from z0 to
    z1, its front and edges (the back lies on the leaf)."""
    add_face(faces, [(x, y, z1) for x, y in pts2], tag, None, (0.0, 0.0, 1.0))
    n = len(pts2)
    cx = sum(x for x, _ in pts2) / n
    cy = sum(y for _, y in pts2) / n
    for i in range(n):
        (xa, ya), (xb, yb) = pts2[i], pts2[(i + 1) % n]
        quad = [(xa, ya, z0), (xb, yb, z0), (xb, yb, z1), (xa, ya, z1)]
        nrm = (yb - ya, -(xb - xa), 0.0)
        mx, my = (xa + xb) / 2 - cx, (ya + yb) / 2 - cy
        if nrm[0] * mx + nrm[1] * my < 0:
            nrm = (-nrm[0], -nrm[1], 0.0)
        add_face(faces, quad, tag, None, nrm)


KNOCKER_SCALE = 1.6  # the bird drawn in KNOCKER_* units below, scaled about its feet


def knocker(faces, y: float, z: float) -> None:
    """The bronze bird knocker on a leaf's face at z, its feet at height y
    (x = 0): a long-legged wading bird in profile, wings folded, standing on
    a bar, its beak holding the ring that hangs below it (low relief, in
    steps; every part bronze, so the shapes read by their outlines and the
    toon shading of their edges)."""
    t = BRONZE
    k = KNOCKER_SCALE

    def plate(pts, z0, z1):
        _tri_prism(faces, [(x * k, y + yy * k) for x, yy in pts], z + z0, z + z1, t)

    plate([(-0.07, -0.04), (0.16, -0.04), (0.16, 0.0), (-0.07, 0.0)], 0.0, 0.03)  # the bar
    for x in (-0.03, 0.035):  # legs (proud of the leaf's middle strap, which they cross)
        plate([(x - 0.012, 0.0), (x + 0.012, 0.0), (x + 0.012, 0.15), (x - 0.012, 0.15)], 0.0, 0.035)
    plate([(-0.09, 0.2), (-0.02, 0.13), (0.15, 0.09), (0.07, 0.2), (0.0, 0.3), (-0.07, 0.29)], 0.0, 0.06)  # body
    plate([(-0.05, 0.18), (0.12, 0.11), (0.03, 0.23), (-0.03, 0.26)], 0.06, 0.085)  # the folded wing
    plate([(-0.075, 0.28), (-0.04, 0.29), (-0.025, 0.44), (-0.06, 0.44)], 0.0, 0.045)  # neck
    plate([(-0.085, 0.44), (-0.02, 0.44), (-0.01, 0.48), (-0.045, 0.51), (-0.085, 0.49)], 0.0, 0.06)  # head
    plate([(-0.085, 0.455), (-0.085, 0.485), (-0.19, 0.445)], 0.0, 0.035)  # beak
    # The ring, hanging from a short loop under the beak's tip.
    tip = (-0.18 * k, y + 0.445 * k)
    r0, r1, n = 0.1, 0.13, 12
    cx, cy = tip[0], tip[1] - 0.04 - r1
    _tri_prism(faces, [(cx - 0.012, cy + r1 - 0.01), (cx + 0.012, cy + r1 - 0.01), (cx + 0.012, tip[1]), (cx - 0.012, tip[1])],
               z, z + 0.02, t)
    ring_in = [(cx + r0 * math.sin(2 * math.pi * i / n), cy + r0 * math.cos(2 * math.pi * i / n)) for i in range(n + 1)]
    ring_out = [(cx + r1 * math.sin(2 * math.pi * i / n), cy + r1 * math.cos(2 * math.pi * i / n)) for i in range(n + 1)]
    band: list = []
    geom.polyline_band(band, ring_in, ring_out, 0.0, 0.04, t, 0.25, caps=(False, False))
    for q in band:
        faces.append(geom.KFace([(x, yy, zz + z + 0.02) for x, yy, zz in q.pts], q.tag, None, q.anchor))


def knocker_door() -> Piece:
    """`kit_door_knocker_a`: the closed oak pair (openings.door_oak) KNOCKER_INSET
    into the passage, a knocker on each leaf's face. Walk-through like the
    greybox leaf it replaces (the door is the common room's gate).

    Knocker door opens as a pair: every face is tagged with its leaf's
    sub-group (grid.LEAF_TAG: "l" for x < 0, "r" for x > 0), so each leaf
    is its own moving node; the birds stand KNOCKER_X off the meeting stile,
    the left one mirrored, facing each other. `meta["hinges"]`: each leaf's
    hinge (its outer edge on the back face, at the floor), its node's origin."""
    w, h = KNOCKER_DOOR_SIZE
    p = openings.door_oak(w, h, open=False, inset=KNOCKER_INSET)
    for f in p.faces:
        cx = sum(pt[0] for pt in f.pts) / len(f.pts)
        f.group = G.LEAF_TAG.format("l" if cx < 0 else "r")
    bird: list = []
    knocker(bird, KNOCKER_Y, -KNOCKER_INSET)
    for leaf, sx in (("l", -1.0), ("r", 1.0)):
        for f in bird:
            pts = [(sx * (x + KNOCKER_X), y, z) for x, y, z in f.pts]
            if sx < 0:
                pts.reverse()  # mirrored: keep the faces' fronts outward
            p.faces.append(geom.KFace(pts, f.tag, None, f.anchor, G.LEAF_TAG.format(leaf)))
    knocker_leaf_edges(p.faces, w, h)
    p.walkthrough = True
    back = -KNOCKER_INSET - KNOCKER_LEAF_THICK
    p.meta.update(inset=KNOCKER_INSET, hinges={"l": (-w / 2.0, 0.0, back), "r": (w / 2.0, 0.0, back)})
    return p


KNOCKER_X = 0.5  # each bird's feet this far off the meeting stile (its ring 8 cm off it)
KNOCKER_LEAF_THICK = 0.08  # openings.door_oak's leaves


def knocker_leaf_edges(faces, w: float, h: float) -> None:
    """Portrait back (the entrances seen from their common rooms): each leaf's edges
    (outer stile, meeting stile, the pointed head; not the bottom on the floor), so
    a leaf turned open reads as an 8 cm oak slab, not two sheets with light between
    them. openings.door_oak's closed outlines; each edge in its leaf's sub-group."""
    spring = G.arch_springing(w, h)
    half = w / 2.0
    arc = geom.pointed_arc(w, spring, openings.SEG)
    left = [(-half, 0.0), (0.0, 0.0), (0.0, h)] + list(reversed(arc[: openings.SEG + 1]))[1:]
    right = [(0.0, 0.0), (half, 0.0)] + list(reversed(arc[openings.SEG:]))
    z0, z1 = -KNOCKER_INSET, -KNOCKER_INSET - KNOCKER_LEAF_THICK
    for leaf, outline in (("l", left), ("r", right)):
        n = len(outline)
        for i in range(n):
            (xa, ya), (xb, yb) = outline[i], outline[(i + 1) % n]
            if ya < 1e-9 and yb < 1e-9:
                continue  # the bottom stands on the floor
            edge: list = []
            add_face(edge, [(xa, ya, z0), (xb, yb, z0), (xb, yb, z1), (xa, ya, z1)], "castle_wood|trim:door_planks",
                     [(ya, 0.0), (yb, 0.0), (yb, 0.08), (ya, 0.08)], (yb - ya, -(xb - xa), 0.0))
            for e in edge:
                e.group = G.LEAF_TAG.format(leaf)
            faces.extend(edge)
