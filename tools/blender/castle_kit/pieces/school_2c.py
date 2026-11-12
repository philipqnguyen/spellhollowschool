"""Classroom pieces (docs/architecture.md):
the dressing of the Defence drill floor (`class_defence`,
world/school_rooms_defence.py) and of the Divination tower room
(`class_divination`, world/school_rooms_divination.py).

* `kit_practice_target`: a free-standing practice target for the Defence
  drills: an oak post on a cross foot carrying a round straw target faced
  with painted rings (facing +z).
* `kit_drill_ring`: the drill circle let into the floor round the
  professor's spot, two brass bands and eight spokes, after the Dueling
  Club stage's ring and star (kit_school.duel_stage); flat (under 2 cm), so
  people walk and stand on it.
* `kit_divination_set`: what stands on one round table (`kit_table_round`)
  in the Divination lesson: per student place a teacup on its saucer with
  the leaves in it, or a small crystal ball on a brass stand; in the middle
  a teapot, a candle or the professor's large crystal ball.

Frames as props.json `kit`: origin at the bottom centre (the table set's at
the middle of the table top), yaw 0 facing +z. Invented everyday objects;
colours are palette names only (castle_textures.json `palette`, each with
its props_slots row; no colour literal here, PaletteRuleTests)."""

from __future__ import annotations

import math

import meshkit as mk
from castle_kit import fire as FI
from castle_kit.pieces.geom import Piece
from castle_kit.pieces.hall import from_mesh

# Zone -> palette name; TILE: the props-atlas tile each zone's faces use.
OAK, STRAW, RING_RED, RING_WHITE, GILT, BRASS, CUP, LEAVES, BALL, CANDLE, IRON = range(1, 12)
ZONES = {OAK: "oak", STRAW: "parcel_tan", RING_RED: "plume", RING_WHITE: "letter_white", GILT: "gilt", BRASS: "brass",
         CUP: "letter_white", LEAVES: "owl_brown", BALL: "hourglass_glass", CANDLE: "glow_candle", IRON: "iron"}
TILE = {OAK: "wood", STRAW: "fabric", RING_RED: "fabric", RING_WHITE: "fabric", GILT: "metal", BRASS: "metal",
        CUP: "fabric", LEAVES: "fabric", BALL: "glass", CANDLE: "glow", IRON: "metal"}

PRACTICE_TARGET_TRIS = 400
DRILL_RING_TRIS = 800
DIVINATION_SET_TRIS = 900

TARGET_CENTRE = 1.35  # the target's centre over the floor
TARGET_R = 0.45
DRILL_RING_H = 0.015  # the inlay's top over the floor (a rug is 0.02)
SET_PLACE_R = 0.47  # a place's distance from the table's middle (the top is 0.7 across)
SET_PLACES_MAX = 4


def _box(m, lo, hi, zone, skip=("-y",)):
    mk.box(m, lo, hi, TILE[zone], zone, skip=skip)


def _lathe(m, rings, seg, zone, centre=(0.0, 0.0, 0.0), cap_top=False):
    mk.lathe(m, rings, seg, TILE[zone], zone, centre, cap_top=cap_top)


# -- the practice target ------------------------------------------------------------------------


def practice_target() -> Piece:
    """`kit_practice_target`: an oak post (1.9 m) on a cross foot with a
    round straw target, TARGET_R across its face, centred TARGET_CENTRE up and
    faced with four painted rings (red, white, red, a gilt boss), facing +z.
    Within 0.5 m of the origin in plan. Walk-through. At most
    PRACTICE_TARGET_TRIS."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    # The foot: two crossed oak beams, the post, a brace under the target.
    _box(m, (-0.45, 0.0, -0.05), (0.45, 0.08, 0.05), OAK)
    _box(m, (-0.05, 0.0, -0.45), (0.05, 0.07, 0.45), OAK)  # a little lower: no shared top with the other beam
    _box(m, (-0.05, 0.08, -0.14), (0.05, 1.9, -0.04), OAK)
    for sx in (-1.0, 1.0):
        mk.rod(m, (sx * 0.38, 0.08, -0.09), (sx * 0.04, 0.55, -0.09), 0.025, 4, TILE[OAK], OAK)
    # The target: built round +y (its face up), then stood up to face +z.
    mark = m.mark()
    t = 0.12
    _lathe(m, [(0.0, 0.0), (TARGET_R, 0.0)], 16, STRAW)  # the back (an outward profile faces -y)
    _lathe(m, [(TARGET_R, 0.0), (TARGET_R, t)], 16, STRAW)  # the rim
    for r0, r1, zone in ((TARGET_R, 0.34, RING_RED), (0.34, 0.23, RING_WHITE), (0.23, 0.12, RING_RED)):
        _lathe(m, [(r0, t), (r1, t)], 16, zone)  # the painted face (an inward profile faces +y)
    _lathe(m, [(0.12, t), (0.0, t + 0.03)], 16, GILT)  # the boss
    # Stand it up: +y to +z (x, y, z) -> (x, -z, y), its back on the post's face.
    m.transform(mark, lambda q: (q[0], TARGET_CENTRE - q[2], q[1] - 0.04))
    p.faces.extend(from_mesh(m, ZONES, walkthrough=True).faces)
    p.meta.update(height=1.9, target=(TARGET_CENTRE, TARGET_R))
    return p


# -- the drill ring -----------------------------------------------------------------------------


def drill_ring(radius: float = 1.6) -> Piece:
    """`kit_drill_ring`: a brass band `radius` round its origin (0.1 m wide),
    an inner band at 0.62 of it and eight spokes between them, DRILL_RING_H
    over the floor (flat: stood on and walked over, like a rug). At most
    DRILL_RING_TRIS."""
    if not 0.8 <= radius <= 3.0:
        raise ValueError("kit_drill_ring: radius 0.8..3 m")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    h = DRILL_RING_H
    for ro, ri in ((radius, radius - 0.1), (radius * 0.62, radius * 0.62 - 0.06)):
        _lathe(m, [(ro, 0.0), (ro, h), (ri, h)], 32, BRASS)
    for k in range(8):
        a = 2 * math.pi * k / 8
        ca, sa = math.cos(a), math.sin(a)
        mark = m.mark()
        r0, r1 = radius * 0.62 + 0.01, radius - 0.12  # clear of the bands' polygons, a little lower than them
        _box(m, (r0, 0.0, -0.025), (r1, h * 0.7, 0.025), BRASS, skip=("-y",))
        m.transform(mark, lambda q, ca=ca, sa=sa: (q[0] * ca - q[2] * sa, q[1], q[0] * sa + q[2] * ca))
    p.faces.extend(from_mesh(m, ZONES, walkthrough=True).faces)
    p.meta.update(radius=radius, height=h)
    return p


# -- the Divination table set -------------------------------------------------------------------


def _teacup(m, x: float, z: float, turn: float) -> None:
    c, s = math.cos(turn), math.sin(turn)
    mark = m.mark()
    _lathe(m, [(0.0, 0.0), (0.075, 0.0), (0.08, 0.008)], 10, CUP, cap_top=True)  # the saucer
    _lathe(m, [(0.0, 0.011), (0.032, 0.011), (0.048, 0.034), (0.054, 0.07)], 10, CUP)
    _lathe(m, [(0.054, 0.07), (0.046, 0.066), (0.02, 0.03), (0.0, 0.028)], 10, LEAVES)  # the leaves in the cup
    _box(m, (0.052, 0.028, -0.006), (0.075, 0.058, 0.006), CUP)  # the handle
    m.transform(mark, lambda q: (x + q[0] * c + q[2] * s, q[1], z - q[0] * s + q[2] * c))


def _ball(m, x: float, z: float, r: float, seg: int) -> None:
    """A crystal ball of radius r on a three-footed brass stand."""
    _lathe(m, [(0.0, 0.0), (r * 0.75, 0.0), (r * 0.8, r * 0.12), (r * 0.55, r * 0.35)], seg, BRASS, (x, 0.0, z),
           cap_top=True)
    rings = [(r * math.sin(math.pi * k / 6), r * 0.2 + r - r * math.cos(math.pi * k / 6)) for k in range(7)]
    _lathe(m, rings, seg, BALL, (x, 0.0, z))


def _teapot(m) -> None:
    _lathe(m, [(0.0, 0.0), (0.07, 0.0), (0.1, 0.06), (0.09, 0.12), (0.05, 0.15)], 12, CUP, cap_top=True)
    _lathe(m, [(0.05, 0.15), (0.045, 0.16), (0.012, 0.17), (0.012, 0.19), (0.0, 0.2)], 8, GILT)  # the lid's knob
    mk.rod(m, (0.08, 0.06, 0.0), (0.17, 0.15, 0.0), 0.012, 5, TILE[CUP], CUP)  # the spout
    _box(m, (-0.13, 0.04, -0.008), (-0.09, 0.13, 0.008), CUP)  # the handle


def _candle(m) -> None:
    _lathe(m, [(0.0, 0.0), (0.05, 0.0), (0.055, 0.012), (0.02, 0.018)], 8, BRASS, cap_top=True)
    mk.cylinder(m, (0.0, 0.018, 0.0), 0.018, 0.14, 8, TILE[RING_WHITE], RING_WHITE)
    FI.note(m, candles=[(0.0, 0.158, 0.0, 0.06)])  # the flame: the client's animated fire


def divination_set(kind: float = 0, places: float = 3, centre: float = 1, spread: float = 50.0) -> Piece:
    """`kit_divination_set`: on a round table top (origin at its middle),
    `places` (0..SET_PLACES_MAX) student places SET_PLACE_R from the middle
    toward +z, `spread` degrees apart round it, each a teacup on its saucer
    with the leaves in it (`kind` 0) or a small crystal ball on a brass stand
    (`kind` 1); in the middle (`centre`) nothing (0), a teapot (1), a candle
    (2) or the professor's large crystal ball (3). Within 0.6 m of the
    origin, at most 0.4 m high. Walk-through. At most DIVINATION_SET_TRIS."""
    k, n, c = int(round(kind)), int(round(places)), int(round(centre))
    if k not in (0, 1) or not 0 <= n <= SET_PLACES_MAX or c not in (0, 1, 2, 3) or not 20.0 <= spread <= 90.0:
        raise ValueError("kit_divination_set: kind 0..1, places 0..4, centre 0..3, spread 20..90 degrees")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    for i in range(n):
        a = math.radians(spread * (i - (n - 1) / 2.0))
        x, z = SET_PLACE_R * math.sin(a), SET_PLACE_R * math.cos(a)
        if k == 0:
            _teacup(m, x, z, a + 0.9)
        else:
            _ball(m, x, z, 0.065, 8)
    if c == 1:
        _teapot(m)
    elif c == 2:
        _candle(m)
    elif c == 3:
        _ball(m, 0.0, 0.0, 0.16, 12)
    built = from_mesh(m, ZONES, walkthrough=True)
    p.faces.extend(built.faces)
    # The candle's flame (meta "candles", the client's animated fire): from_mesh carries the mesh's notes.
    if built.meta.get("candles"):
        p.meta["candles"] = built.meta["candles"]
    p.meta.update(places=n, kind=k)
    return p
