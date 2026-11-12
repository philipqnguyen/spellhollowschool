"""Classroom pieces: the dressing of the shared classroom
instance room `class_classroom` (tools/blender/world/class_rooms.py,
docs/architecture.md).

`kit_desk_objects`: what lies on one study table (`kit_table_study`, 1.6 m)
in the Transfiguration lesson: per student place a book, an inkwell with a
quill and one small practice object (a teacup, a matchbox, a candle, a key,
a spoon, a pincushion or a button, the objects of the lesson's puzzles).
Origin at the middle of the table top, the students' side +z. Invented
everyday objects; colours are palette names only (castle_textures.json
`palette`, existing props-atlas slots; no colour literal here,
PaletteRuleTests)."""

from __future__ import annotations

import math

import meshkit as mk
from castle_kit import fire as FI
from castle_kit.pieces.geom import Piece
from castle_kit.pieces.hall import from_mesh

# Zone -> palette name (castle_textures.json `palette`, each with its props_slots row).
WHITE, TAN, INK, PLUME, BRASS, STEEL, WINE, CANDLE, PEWTER = range(1, 10)
BOOKS = (10, 11, 12, 13, 14)
ZONES = {WHITE: "letter_white", TAN: "parcel_tan", INK: "ink_blue", PLUME: "plume", BRASS: "brass", STEEL: "steel",
         WINE: "wine_red", CANDLE: "glow_candle", PEWTER: "pewter", 10: "book_red", 11: "book_green", 12: "book_blue",
         13: "book_brown", 14: "book_ochre"}
# The tile each zone's faces use (a props_slots row [tile, "@name"] exists for each pair).
TILE = {WHITE: "fabric", TAN: "fabric", INK: "glass", PLUME: "fabric", BRASS: "metal", STEEL: "metal", WINE: "leather",
        CANDLE: "glow", PEWTER: "metal", **{b: "leather" for b in BOOKS}}

PRACTICE = ("teacup", "matchbox", "candle", "key", "spoon", "pincushion", "button")
PLACES = (-0.4, 0.4)  # the two students' places along the table (x)
DESK_OBJECTS_TRIS = 700  # the piece's cap at LOD0 (two places)


def _rand(seed: int):
    s = (seed * 2654435761 + 12345) % 2**32 or 1
    while True:
        s = (s * 1103515245 + 12345) % 2**31
        yield s


def _box(m, lo, hi, zone, skip=("-y",)):
    mk.box(m, lo, hi, TILE[zone], zone, skip=skip)


def _lathe(m, rings, seg, zone, centre, cap_top=True):
    mk.lathe(m, rings, seg, TILE[zone], zone, centre, cap_top=cap_top)


def _practice(m, kind: str, x: float, z: float, turn: float) -> None:
    """One practice object standing on the table at (x, 0, z)."""
    c, s = math.cos(turn), math.sin(turn)
    mark = m.mark()
    if kind == "teacup":
        _lathe(m, [(0.0, 0.0), (0.07, 0.0), (0.075, 0.008)], 10, WHITE, (0.0, 0.0, 0.0), cap_top=True)  # saucer
        _lathe(m, [(0.0, 0.011), (0.03, 0.011), (0.045, 0.032), (0.05, 0.066)], 10, WHITE, (0.0, 0.0, 0.0), cap_top=False)
        _lathe(m, [(0.05, 0.066), (0.042, 0.061), (0.0, 0.056)], 10, PEWTER, (0.0, 0.0, 0.0), cap_top=False)  # the tea
        _box(m, (0.049, 0.026, -0.006), (0.07, 0.054, 0.006), WHITE)  # handle
    elif kind == "matchbox":
        _box(m, (-0.05, 0.0, -0.03), (0.05, 0.025, 0.03), TAN)
        _box(m, (-0.054, 0.006, -0.034), (0.054, 0.019, 0.034), INK, skip=("-y", "+y"))  # the label band
    elif kind == "candle":
        _lathe(m, [(0.0, 0.0), (0.045, 0.0), (0.05, 0.012), (0.02, 0.016)], 8, BRASS, (0.0, 0.0, 0.0), cap_top=True)
        mk.cylinder(m, (0.0, 0.016, 0.0), 0.016, 0.12, 8, TILE[WHITE], WHITE)
        FI.note(m, candles=[(x, 0.136, z, 0.055)])  # the flame: the client's animated fire
    elif kind == "key":
        _box(m, (-0.07, 0.0, -0.007), (0.035, 0.012, 0.007), BRASS)  # shaft
        _box(m, (-0.07, 0.0, 0.007), (-0.05, 0.01, 0.03), BRASS)  # bit
        _lathe(m, [(0.02, 0.0), (0.03, 0.0), (0.03, 0.009), (0.02, 0.009)], 8, BRASS, (0.065, 0.0, 0.0), cap_top=False)
    elif kind == "spoon":
        _lathe(m, [(0.0, 0.0), (0.025, 0.004), (0.03, 0.012)], 8, STEEL, (0.0, 0.0, 0.0), cap_top=False)
        m.transform(mark, lambda p: (p[0] * 1.5, p[1], p[2]))
        _box(m, (0.045, 0.008, -0.005), (0.13, 0.014, 0.005), STEEL)
    elif kind == "pincushion":
        _lathe(m, [(0.0, 0.0), (0.045, 0.0), (0.05, 0.025), (0.035, 0.045), (0.0, 0.05)], 8, WINE, (0.0, 0.0, 0.0),
               cap_top=False)
        for k in range(3):
            a = 2.1 * k
            mk.rod(m, (0.02 * math.cos(a), 0.04, 0.02 * math.sin(a)), (0.03 * math.cos(a), 0.075, 0.03 * math.sin(a)), 0.003,
                   3, TILE[STEEL], STEEL)
    else:  # button
        _lathe(m, [(0.0, 0.0), (0.035, 0.0), (0.035, 0.008), (0.0, 0.01)], 10, WINE, (0.0, 0.0, 0.0), cap_top=False)
    m.transform(mark, lambda p: (x + p[0] * c + p[2] * s, p[1], z - p[0] * s + p[2] * c))


def desk_objects(seed: float = 0) -> Piece:
    """`kit_desk_objects`: the two places of a 1.6 m study table (PLACES):
    a closed book near the student's edge, an inkwell with a quill toward
    the far edge, and a practice object of PRACTICE between them, chosen and
    turned by `seed`. Origin at the table top's middle; everything within
    0.75 m along the table and 0.4 m across, at most 0.27 m high. Walk-through.
    At most DESK_OBJECTS_TRIS."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    r = _rand(int(round(seed)))
    for x in PLACES:
        v = [next(r) for _ in range(5)]
        # A closed book, a little turned, near the student's edge.
        book = BOOKS[v[0] % len(BOOKS)]
        bx = x - 0.12 + 0.04 * ((v[1] >> 4) % 3)
        mark = m.mark()
        _box(m, (-0.1, 0.0, -0.08), (0.11, 0.008, 0.08), book)
        _box(m, (-0.1, 0.008, -0.075), (0.105, 0.032, 0.075), WHITE)
        _box(m, (-0.1, 0.032, -0.08), (0.11, 0.04, 0.08), book)
        _box(m, (-0.11, 0.0, -0.08), (-0.1, 0.04, 0.08), book)  # the spine
        turn = ((v[2] >> 5) % 30 - 15) * math.pi / 180.0
        cc, ss = math.cos(turn), math.sin(turn)
        m.transform(mark, lambda q, bx=bx: (bx + q[0] * cc + q[2] * ss, q[1], 0.22 - q[0] * ss + q[2] * cc))
        # An inkwell and a quill toward the far edge.
        ix, iz = x + 0.27, -0.3
        _lathe(m, [(0.0, 0.0), (0.03, 0.0), (0.032, 0.03), (0.014, 0.045), (0.014, 0.055)], 8, INK, (ix, 0.0, iz))
        q0, q1 = (ix + 0.005, 0.05, iz), (ix + 0.09, 0.26, iz + 0.05)
        mk.rod(m, q0, q1, 0.004, 3, TILE[PLUME], PLUME)
        vane = [(ix + 0.04, 0.12, iz + 0.024), (ix + 0.09, 0.26, iz + 0.05), (ix + 0.1, 0.24, iz + 0.05),
                (ix + 0.06, 0.13, iz + 0.026)]
        m.poly(vane, TILE[PLUME], [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], PLUME)
        m.poly(list(reversed(vane)), TILE[PLUME], [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], PLUME)
        # The practice object.
        kind = PRACTICE[v[3] % len(PRACTICE)]
        _practice(m, kind, x + 0.02, -0.08, (v[4] % 628) / 100.0)
    p.faces.extend(from_mesh(m, ZONES, walkthrough=True).faces)
    p.meta.update(places=list(PLACES))
    return p
