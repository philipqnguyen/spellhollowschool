"""Pieces (docs/art/castle-kit.md; docs/architecture.md
"Secrets and the map"): the walk-through entrances of the five hidden passages, the
Wishing Room's door, the passages' own dressing (the sloping ceiling over
their stairs, rubble) and the Lost Property Vault's unclaimed oddments.

Frames as geom.py: x along the wall (to the right seen from the room), y up,
z out of the wall face into the room; wall pieces have their back on the
wall plane z = 0 and their origin at the bottom centre; floor pieces their
origin at the bottom centre.

Every entrance (`kit_entrance_*`) covers a passage door's 2 x 3.5 m pointed
opening on its public side and is drawn wholly on the props atlas
(castle_props), so the assembler can put it in one moving node
(grid.ENTRANCE_NODE, one draw); each is walk-through (walls stay the absence
of ground; the client sways the cloth or slides the panel while someone stands
on the door surface). Pieces built from other pieces (the trophy case, the
scholar statue) have their few non-props faces re-tagged onto the atlas.

Everything is our own invented design: the two tapestries ("the Heron at the
Ford", "the Climbing Stair"), the two paintings ("the Lantern Tower", "a
Reader's Still Life") and the door. Colours: palette names only
(grid.palette_tag; the props slots and the two passage stones appended under
`_doc_p3d_secrets` in castle_textures.json); no colour literal here."""

from __future__ import annotations

import math
import os
import random

import meshkit as mk
from castle_kit import grid as G
from castle_kit.pieces import frames, geom
from castle_kit.pieces.geom import Piece, add_face
from castle_kit.pieces.hall import from_mesh

WALL = "castle_wall|world"

# Zone -> palette name (castle_textures.json `palette`; each [tile, "@name"] a props slot).
(OAK, OAK_AGED, IRON, GILT, BRASS, STONE_WARM, STONE_DUNGEON, STATUE, NAVY, OCHRE, CREAM, WINE, SKY, FOREST, LEAF,
 UMBER, ROSE, GLOW, CANDLE, LEATHER, BOOK_RED, BOOK_GREEN, BOOK_BLUE, BOOK_BROWN, BOOK_OCHRE, PEWTER, COPPER,
 LETTER, PARCEL, CANVAS, HOUSE_A, HOUSE_B, HOUSE_C, HOUSE_D, SOOT, BRONZE, ROCK, ROCK_LIGHT) = range(1, 39)
ZONES = {OAK: "oak", OAK_AGED: "oak_aged", IRON: "iron", GILT: "gilt", BRASS: "brass",
         STONE_WARM: "passage_stone_warm", STONE_DUNGEON: "passage_stone_dungeon", STATUE: "statue_stone",
         NAVY: "tapestry_navy", OCHRE: "tapestry_ochre", CREAM: "tapestry_cream", WINE: "tapestry_wine",
         SKY: "tapestry_sky", FOREST: "tapestry_forest", LEAF: "tapestry_leaf", UMBER: "tapestry_umber",
         ROSE: "tapestry_rose", GLOW: "glow_lantern", CANDLE: "glow_candle", LEATHER: "upholstery_leather",
         BOOK_RED: "book_red", BOOK_GREEN: "book_green", BOOK_BLUE: "book_blue", BOOK_BROWN: "book_brown",
         BOOK_OCHRE: "book_ochre", PEWTER: "pewter", COPPER: "copper", LETTER: "letter_white", PARCEL: "parcel_tan",
         CANVAS: "canvas", HOUSE_A: "house_a", HOUSE_B: "house_b", HOUSE_C: "house_c", HOUSE_D: "house_d",
         SOOT: "soot", BRONZE: "bronze", ROCK: "crag_rock", ROCK_LIGHT: "grounds_rock"}
STONES = (STONE_WARM, STONE_DUNGEON)  # `stone` args 0 and 1: the warm castle stone, the dungeon stone

DOOR_W, DOOR_H = 2.0, 3.5  # every passage door (layout: 2 m wide, 3.5 m ceiling)
SEG = 8  # arc segments per side of a pointed outline


def _finish(p: Piece, m: mk.Mesh) -> Piece:
    p.faces.extend(from_mesh(m, ZONES, walkthrough=p.walkthrough).faces)
    return p


def _rect(m: mk.Mesh, x0, y0, x1, y1, z, zone, tile="fabric"):
    m.poly([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)], tile, zone=zone)


def _poly(m: mk.Mesh, pts2, z, zone, tile="fabric"):
    """A flat polygon facing +z from 2D points (any winding)."""
    pts = [(x, y, z) for x, y in pts2]
    if geom.newell(pts)[2] < 0:
        pts.reverse()
    m.poly(pts, tile, zone=zone)


def _lozenge(m, cx, cy, rx, ry, z, zone, tile="fabric"):
    _poly(m, [(cx, cy - ry), (cx + rx, cy), (cx, cy + ry), (cx - rx, cy)], z, zone, tile)


def _disc(m, cx, cy, r, z, zone, tile="fabric", n=12):
    _poly(m, [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)], z,
          zone, tile)


def retag_props(faces, tag: str) -> None:
    """Re-tags every face not on the props atlas onto it (`tag`, a palette
    tag): planar UVs computed by the assembler."""
    for f in faces:
        if G.parse_face_tag(f.tag).material != "castle_props":
            f.tag = tag
            f.uv = None


# =====================================================================================
# The tapestry (kit_entrance_tapestry): P1 at both ends
# =====================================================================================

TAPESTRY_SIZE = (2.6, 3.75)  # cloth width x height: covers the 2 x 3.5 opening with 0.3 m to spare
TAPESTRY_FOOT = 0.06  # the cloth's foot above the floor (a floor-length hanging)
TAPESTRY_SUBJECTS = 2


def entrance_tapestry(subject: float = 0) -> Piece:
    """`kit_entrance_tapestry`: a floor-length hanging over a passage door,
    flat cloth on the props atlas (as kit_tapestry_weaver), our own subjects:
    0 "the Heron at the Ford" (a heron on one leg among reeds at a ford under
    a low ochre sun, on a pale sky, in a wine border of ochre lozenges);
    1 "the Climbing Stair" (a stair of ochre steps winding up a navy night to
    a cream star, in a forest border of cream lozenges). A fringe at the
    foot, an iron rod with gilt finials above. Walk-through. Origin: the
    cloth's bottom centre on the wall face; at most grid.TAPESTRY_PROUD off it."""
    s = int(round(subject))
    if not 0 <= s < TAPESTRY_SUBJECTS:
        raise ValueError(f"kit_entrance_tapestry: subject 0..{TAPESTRY_SUBJECTS - 1}")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    w, h = TAPESTRY_SIZE
    hw, y0, y1 = w / 2.0, TAPESTRY_FOOT, TAPESTRY_FOOT + h
    z0, z1, z2, z3 = 0.03, 0.034, 0.037, 0.04
    field, border, loz = ((SKY, WINE, OCHRE), (NAVY, FOREST, CREAM))[s]
    _rect(m, -hw, y0, hw, y1, z0, field)
    m.poly([(-hw, y0, z0 - 0.005), (-hw, y1, z0 - 0.005), (hw, y1, z0 - 0.005), (hw, y0, z0 - 0.005)], "fabric",
           zone=field)
    bw = 0.17
    for a, b, c, d in ((-hw, y0, hw, y0 + bw), (-hw, y1 - bw, hw, y1), (-hw, y0 + bw, -hw + bw, y1 - bw),
                       (hw - bw, y0 + bw, hw, y1 - bw)):
        _rect(m, a, b, c, d, z1, border)
    for k in range(8):
        y = y0 + bw + (h - 2 * bw) * (k + 0.5) / 8
        _lozenge(m, -hw + bw / 2, y, 0.05, 0.08, z2, loz)
        _lozenge(m, hw - bw / 2, y, 0.05, 0.08, z2, loz)
    for k in range(5):
        x = -hw + bw + (w - 2 * bw) * (k + 0.5) / 5
        _lozenge(m, x, y0 + bw / 2, 0.08, 0.05, z2, loz)
        _lozenge(m, x, y1 - bw / 2, 0.08, 0.05, z2, loz)
    ix0, ix1, iy0, iy1 = -hw + bw, hw - bw, y0 + bw, y1 - bw
    # The picture's layers 4 mm apart (more than the lint's 2 mm plane bins): shapes overlap only across layers.
    Z = [z1 + 0.004 * i for i in range(8)]
    if s == 0:
        # The ford: water across the lower third, the reed beds either side, the sun, the heron.
        _rect(m, ix0, iy0, ix1, iy0 + 0.75, Z[0], NAVY)
        _poly(m, [(ix0, iy0 + 0.75), (ix0 + 0.7, iy0 + 0.95), (ix0 + 1.2, iy0 + 0.8), (ix1, iy0 + 1.0), (ix1, iy0 + 0.75)],
              Z[0], FOREST)
        _disc(m, ix1 - 0.55, iy1 - 0.65, 0.3, Z[0], OCHRE)
        for k in range(4):
            _rect(m, ix0 + 0.25 + 0.4 * k, iy0 + 0.25 + 0.12 * (k % 2), ix0 + 0.5 + 0.4 * k, iy0 + 0.28 + 0.12 * (k % 2),
                  Z[1], SKY)
        for k in range(7):
            ry = iy0 + 0.35 + 0.08 * (k % 3)
            _rect(m, ix0 + 0.12 + 0.11 * k, ry, ix0 + 0.15 + 0.11 * k, ry + 1.1 + 0.18 * (k % 2), Z[2], LEAF)
            _rect(m, ix1 - 0.15 - 0.1 * k, ry, ix1 - 0.12 - 0.1 * k, ry + 0.95 + 0.2 * (k % 2), Z[2], LEAF)
            _lozenge(m, ix0 + 0.135 + 0.11 * k, ry + 1.15 + 0.18 * (k % 2), 0.035, 0.12, Z[3], UMBER)
        cx, by = 0.05, iy0 + 0.55
        _rect(m, cx - 0.02, by, cx + 0.01, by + 0.85, Z[2], UMBER)  # the standing leg
        _poly(m, [(cx + 0.01, by + 0.62), (cx + 0.2, by + 0.48), (cx + 0.21, by + 0.52), (cx + 0.03, by + 0.68)], Z[3], UMBER)
        body = [(cx - 0.32, by + 0.98), (cx - 0.12, by + 0.86), (cx + 0.18, by + 0.88), (cx + 0.32, by + 1.02),
                (cx + 0.16, by + 1.2), (cx - 0.1, by + 1.22)]
        _poly(m, body, Z[4], CREAM)
        _poly(m, [(cx - 0.32, by + 0.98), (cx - 0.55, by + 0.9), (cx - 0.3, by + 1.06)], Z[5], CREAM)  # the tail
        _poly(m, [(cx - 0.18, by + 1.0), (cx + 0.12, by + 0.96), (cx + 0.2, by + 1.08), (cx - 0.06, by + 1.12)], Z[5], SKY)
        _poly(m, [(cx + 0.18, by + 1.14), (cx + 0.24, by + 1.17), (cx + 0.08, by + 1.62), (cx + 0.02, by + 1.6)], Z[6],
              CREAM)  # the neck
        _disc(m, cx + 0.07, by + 1.64, 0.08, Z[7] - 0.002, CREAM, n=8)
        _poly(m, [(cx + 0.13, by + 1.66), (cx + 0.42, by + 1.6), (cx + 0.13, by + 1.62)], Z[7] + 0.002, OCHRE)  # the bill
    else:
        # The stair: ochre steps winding up from the foot to a star near the top; small stars.
        steps = 9
        for k in range(steps):
            t = k / (steps - 1)
            x = 0.55 * math.sin(t * math.pi * 1.6)
            y = iy0 + 0.2 + (iy1 - iy0 - 1.0) * t
            _rect(m, x - 0.32 + 0.1 * t, y, x + 0.32 - 0.1 * t, y + 0.13, Z[0], OCHRE)
            _rect(m, x - 0.32 + 0.1 * t, y - 0.12, x + 0.32 - 0.1 * t, y, Z[0], UMBER)
        sx, sy = 0.55 * math.sin(math.pi * 1.6), iy1 - 0.45
        _lozenge(m, sx, sy, 0.12, 0.26, Z[1], CREAM)
        _lozenge(m, sx, sy, 0.26, 0.12, Z[2], CREAM)
        rng = random.Random(3401)
        placed = [(sx, sy, 0.3)]
        while len(placed) < 15:
            x, y = rng.uniform(ix0 + 0.1, ix1 - 0.1), rng.uniform(iy0 + 0.5, iy1 - 0.15)
            r = rng.uniform(0.025, 0.05)
            if any(math.hypot(x - px, y - py) < pr + r * 1.6 + 0.05 for px, py, pr in placed):
                continue
            placed.append((x, y, r * 1.6))
            _lozenge(m, x, y, r, r * 1.6, Z[3], OCHRE if rng.random() < 0.6 else CREAM)
    for i in range(int(w / 0.065)):
        x = -hw + 0.03 + 0.065 * i
        _rect(m, x - 0.013, y0 - 0.06, x + 0.013, y0, z0, CREAM if i % 2 else loz)
    ry, rz = y1 + 0.03, 0.05
    mk.rod(m, (-hw - 0.12, ry, rz), (hw + 0.12, ry, rz), 0.022, 6, "metal", IRON)
    for sx in (-1.0, 1.0):
        mk.lathe(m, [(0.0, -0.05), (0.045, -0.02), (0.045, 0.02), (0.0, 0.05)], 6, "metal", GILT, (sx * (hw + 0.16), ry, rz))
        mk.box(m, (sx * (hw - 0.1) - 0.025, ry - 0.08, 0.0), (sx * (hw - 0.1) + 0.025, ry, rz), "metal", IRON, skip=("-z",))
    p.meta.update(width=w, height=y1 + 0.08, proud=rz + 0.045, subject=s)
    return _finish(p, m)


# =====================================================================================
# The panel (kit_entrance_panel): P2's trophy case, and a plain oak panel
# =====================================================================================

PANEL_SIZE = (2.4, 3.9)  # the plain oak panel: width x height


CASE_BACK_Z = 0.016  # between the board's back (frames.BOARD_Z) and the trophy case's own back (0.02)


def entrance_panel(case: float = 1, seed: float = 107, house: float = 3) -> Piece:
    """`kit_entrance_panel`: `case` 1, a sliding panel styled as the Trophy
    Room's cases (kit_trophy_case, 2.4 m, `seed` and `house` as there; its
    oak re-tagged onto the props atlas); `case` 0, a plain oak wall panel
    2.4 x 3.9 m (a raised and fielded panel in a moulded frame, two rails, a
    small brass ring pull). Walk-through; the client slides it along the wall.
    Origin: the bottom centre on the wall face."""
    if int(round(case)) == 1:
        from castle_kit.pieces import rooms_upper

        p = rooms_upper.trophy_case(width=2.4, seed=seed, house=house)
        retag_props(p.faces, G.palette_tag("wood", "oak"))
        p.walkthrough = True
        p.meta["style"] = "case"
        frames.board_back(p.faces, -1.2, 1.2, 0.0, p.meta["height"])  # Portrait back: seen from the passage
        # ... and the case's own back has a 4 cm slit under its cornice (the passage showed
        # through from the Trophy Room): a dark board facing the room just behind it.
        top = p.meta["height"]
        add_face(p.faces, [(-1.2, 0.0, CASE_BACK_Z), (1.2, 0.0, CASE_BACK_Z), (1.2, top, CASE_BACK_Z), (-1.2, top, CASE_BACK_Z)],
                 frames.PLANK_TAG, None, (0.0, 0.0, 1.0))
        return p
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    w, h = PANEL_SIZE
    hw = w / 2.0
    d = 0.08
    mk.box(m, (-hw, 0.0, 0.0), (hw, h, d), "wood", OAK, skip=("-z", "-y"))
    # Three stiles, the rails between them (their ends meet the stiles' sides), the fields.
    rails = (0.0, 1.0, 2.5, h)
    stiles = (-hw, 0.0, hw)
    for x in stiles:
        mk.box(m, (max(-hw, x - 0.07), 0.0, d), (min(hw, x + 0.07), h, d + 0.035), "wood", OAK_AGED, skip=("-z", "-y"))
    for y in rails:
        for xa, xb in ((-hw + 0.07, -0.07), (0.07, hw - 0.07)):
            mk.box(m, (xa, max(0.0, y - 0.06), d), (xb, min(h, y + 0.06), d + 0.03), "wood", OAK_AGED,
                   skip=("-z", "-y", "-x", "+x"))
    for (ya, yb) in zip(rails, rails[1:]):
        for (xa, xb) in ((-hw + 0.07, -0.07), (0.07, hw - 0.07)):
            a, b = ya + 0.06, yb - 0.06
            mk.box(m, (xa + 0.1, a + 0.1, d), (xb - 0.1, b - 0.1, d + 0.02), "wood", OAK, skip=("-z",))
    mk.lathe(m, [(0.05, -0.012), (0.05, 0.012)], 8, "metal", BRASS, (0.55, 1.0, d + 0.05), cap_top=True)
    p.meta.update(width=w, height=h, proud=d + 0.06, style="plain")
    _finish(p, m)
    frames.board_back(p.faces, -hw, hw, 0.0, h)  # Portrait back: seen from the passage
    return p


# =====================================================================================
# The painting (kit_entrance_painting): P3 at both ends
# =====================================================================================

PAINTING_SIZE = (2.4, 3.7)  # the frame outside: covers the opening with 0.2 m to spare
PAINTING_SUBJECTS = 2


def entrance_painting(subject: float = 0) -> Piece:
    """`kit_entrance_painting`: a tall gilt-framed painting standing on the
    floor over a passage door (as the Fruit Bowl Painting over the kitchens'),
    flat paint on the props atlas's smooth tile, our own subjects: 0 "the
    Lantern Tower" (an evening river winding through hills to a slender tower
    with one lit window under a rose sky); 1 "a Reader's Still Life" (books
    stacked on a wine cloth by a lit candle and an open book, on a dark umber
    ground). Walk-through; the client swings it from its hinge side. Origin:
    the bottom centre on the wall face."""
    s = int(round(subject))
    if not 0 <= s < PAINTING_SUBJECTS:
        raise ValueError(f"kit_entrance_painting: subject 0..{PAINTING_SUBJECTS - 1}")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    w, h = PAINTING_SIZE
    hw = w / 2.0
    fr, zc = 0.16, 0.06  # the frame's width; the canvas plane
    # The frame: four moulded bars (outer band, inner bead), mitred as boxes.
    for x0, y0, x1, y1 in ((-hw, 0.0, hw, fr), (-hw, h - fr, hw, h), (-hw, fr, -hw + fr, h - fr), (hw - fr, fr, hw, h - fr)):
        mk.box(m, (x0, y0, 0.0), (x1, y1, zc + 0.05), "metal", GILT, skip=("-z",))
    for x0, y0, x1, y1 in ((-hw + fr, fr, hw - fr, fr + 0.03), (-hw + fr, h - fr - 0.03, hw - fr, h - fr),
                           (-hw + fr, fr + 0.03, -hw + fr + 0.03, h - fr - 0.03), (hw - fr - 0.03, fr + 0.03, hw - fr, h - fr - 0.03)):
        mk.box(m, (x0, y0, zc), (x1, y1, zc + 0.02), "metal", BRASS, skip=("-z",))
    mk.box(m, (-hw + fr, fr, 0.0), (hw - fr, h - fr, zc), "metal", GILT, skip=("-z", "+z"))
    ix0, ix1, iy0, iy1 = -hw + fr + 0.03, hw - fr - 0.03, fr + 0.03, h - fr - 0.03
    # Paint layers 5 mm apart (more than the lint's 2 mm plane bins): shapes overlap only across layers.
    z = [zc + 0.002 + 0.005 * i for i in range(6)]
    if s == 0:
        # Sky bands, hills, the river, the tower.
        bands = ((ROSE, iy0 + 1.45, iy0 + 1.95), (SKY, iy0 + 1.95, iy0 + 2.7), (NAVY, iy0 + 2.7, iy1))
        for zone, a, b in bands:
            _rect(m, ix0, a, ix1, b, z[0], zone, "glass")
        _rect(m, ix0, iy0, ix1, iy0 + 1.45, z[0], UMBER, "glass")
        _poly(m, [(ix0, iy0 + 1.4), (ix1, iy0 + 1.4), (ix1, iy0 + 1.6), (ix1 - 0.5, iy0 + 1.75), (ix0 + 0.6, iy0 + 1.5),
                  (ix0, iy0 + 1.62)], z[1], UMBER, "glass")
        _poly(m, [(ix0, iy0), (ix1, iy0), (ix1, iy0 + 1.1), (ix0 + 0.9, iy0 + 1.3), (ix0, iy0 + 1.05)], z[2], FOREST, "glass")
        river = [(-0.35, iy0), (0.25, iy0), (0.05, iy0 + 0.35), (0.35, iy0 + 0.7), (0.2, iy0 + 1.05), (0.28, iy0 + 1.2),
                 (0.12, iy0 + 1.2), (0.02, iy0 + 1.05), (0.12, iy0 + 0.72), (-0.2, iy0 + 0.36)]
        right, left = river[1:6], [river[0]] + river[9:5:-1]
        for i in range(4):
            _poly(m, [right[i], right[i + 1], left[i + 1], left[i]], z[3], SKY, "glass")
        tx, ty = 0.35, iy0 + 1.38
        _poly(m, [(tx - 0.42, iy0 + 1.25), (tx + 0.42, iy0 + 1.25), (tx + 0.2, ty + 0.05), (tx - 0.25, ty + 0.02)], z[3],
              LEAF, "glass")
        _rect(m, tx - 0.08, ty, tx + 0.08, ty + 0.95, z[4], CREAM, "glass")
        _poly(m, [(tx - 0.12, ty + 0.95), (tx + 0.12, ty + 0.95), (tx, ty + 1.25)], z[4], WINE, "glass")
        _rect(m, tx - 0.025, ty + 0.68, tx + 0.025, ty + 0.78, z[5], GLOW, "glow")
        _poly(m, [(ix0 + 0.35, iy1 - 0.45), (ix0 + 0.5, iy1 - 0.3), (ix0 + 0.42, iy1 - 0.47)], z[1], CREAM, "glass")
    else:
        _rect(m, ix0, iy0 + 0.95, ix1, iy1, z[0], UMBER, "glass")
        _rect(m, ix0, iy0, ix1, iy0 + 0.95, z[0], WINE, "glass")  # the cloth on the table
        stack = ((BOOK_RED, 0.62, 0.16), (BOOK_GREEN, 0.56, 0.13), (BOOK_BLUE, 0.6, 0.15), (BOOK_OCHRE, 0.5, 0.12))
        y = iy0 + 0.95
        for k, (zone, bw, bh) in enumerate(stack):
            dx = 0.04 * (1 if k % 2 else -1)
            _rect(m, -0.55 + dx - bw / 2, y, -0.55 + dx + bw / 2, y + bh, z[2], zone, "leather")
            _rect(m, -0.55 + dx - bw / 2 + 0.03, y + bh * 0.4, -0.55 + dx + bw / 2 - 0.03, y + bh * 0.55, z[3], OCHRE,
                  "glass")
            y += bh
        _poly(m, [(0.0, iy0 + 0.95), (0.62, iy0 + 0.95), (0.7, iy0 + 1.1), (0.31, iy0 + 1.04), (-0.08, iy0 + 1.1)], z[2],
              CREAM, "glass")  # the open book
        _disc(m, 0.42, iy0 + 1.98, 0.36, z[1], OCHRE, "glass", n=14)  # the candle's light on the ground
        _disc(m, 0.42, iy0 + 1.98, 0.2, z[2], ROSE, "glass", n=12)
        _rect(m, 0.38, iy0 + 1.15, 0.46, iy0 + 1.85, z[3], CREAM, "glass")  # the candle on the book's far side
        _poly(m, [(0.42, iy0 + 1.86), (0.47, iy0 + 1.98), (0.42, iy0 + 2.12), (0.37, iy0 + 1.98)], z[4], CANDLE, "glow")
    p.meta.update(width=w, height=h, proud=zc + 0.05, subject=s)
    _finish(p, m)
    frames.board_back(p.faces, -hw, hw, 0.0, h)  # Portrait back: seen from the passage
    return p


# =====================================================================================
# The statue (kit_entrance_statue): P4 in the Long Gallery
# =====================================================================================

NICHE_BACK = 0.45  # the stone back panel behind the statue: this far behind the wall face


def entrance_statue() -> Piece:
    """`kit_entrance_statue`: the M5b robed scholar on its pedestal standing
    in a passage door, in front of a plain stone back (the cell's warm stone
    tone on the props atlas) closing the opening NICHE_BACK behind the wall
    face, so the doorway reads as a statue's niche; you walk through the
    statue's back. Walk-through. Origin: the bottom centre on the wall face
    (the pedestal's centre 0.3 m in front of it)."""
    from castle_kit.pieces import rooms_upper

    st = rooms_upper.statue_scholar()
    p = Piece("", walkthrough=True)
    for f in st.faces:
        add_face(p.faces, [(x, y, z + 0.3) for x, y, z in f.pts], f.tag, f.uv, geom.newell(f.pts))
    m = mk.Mesh()
    outline = geom.lancet_outline(DOOR_W, DOOR_H, SEG)
    m.poly([(x, y, -NICHE_BACK) for x, y in outline], "stone", zone=STONE_WARM)
    m.poly([(x, y, -NICHE_BACK - 0.02) for x, y in reversed(outline)], "stone", zone=STONE_WARM)
    p.meta.update(height=st.meta.get("height", 3.37), back=NICHE_BACK)
    return _finish(p, m)


# =====================================================================================
# The plain wall (kit_entrance_wall): P5 and the far ends
# =====================================================================================

WALL_BACK = 0.03  # the panel stands this far behind the wall face (never on its plane)


def entrance_wall(stone: float = 1) -> Piece:
    """`kit_entrance_wall`: a stretch of plain stone filling a passage door's
    pointed opening WALL_BACK behind the wall face, drawn both ways (from the
    room and from the passage), in the props atlas's stone in a passage stone
    tone (`stone` 0 the warm castle stone, 1 the dungeon stone): the slightly
    different coursing is the only tell. Walk-through (as common room D's
    kit_door_stone_hidden). Origin: the bottom centre on the wall face."""
    i = int(round(stone))
    if not 0 <= i < len(STONES):
        raise ValueError("kit_entrance_wall: stone 0 or 1")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    outline = geom.lancet_outline(DOOR_W, DOOR_H, SEG)
    m.poly([(x, y, -WALL_BACK) for x, y in outline], "stone", zone=STONES[i])
    m.poly([(x, y, -WALL_BACK - 0.02) for x, y in reversed(outline)], "stone", zone=STONES[i])
    p.meta.update(width=DOOR_W, height=DOOR_H, back=WALL_BACK, stone=i)
    return _finish(p, m)


ENTRANCE_PIECES = {"tapestry": "kit_entrance_tapestry", "panel": "kit_entrance_panel",
                   "painting": "kit_entrance_painting", "statue": "kit_entrance_statue", "wall": "kit_entrance_wall"}


# =====================================================================================
# The Wishing Room's door (kit_wishing_room_door)
# =====================================================================================

RR_DOOR = (2.6, 4.6)  # the leaves' opening: width, apex
RR_SURROUND = 0.32  # the stone surround's band
RR_PROUD = 0.2  # everything within this of the wall face (grid.INTRUSION_MAX)


def wishing_room_door() -> Piece:
    """`kit_wishing_room_door`: the door that appears on the blank wall, our
    own design: a tall pointed pair of dark oak leaves with three iron straps
    ending in fleurs and a brass ring on each leaf, in a plain stone surround
    standing RR_PROUD off the wall (in the warm stone tone). All
    on the props atlas, one node the client keeps hidden until the server
    opens the door. Origin: the bottom centre on the wall face."""
    p = Piece("")
    m = mk.Mesh()
    w, apex = RR_DOOR
    spring = apex - G.ARCH_RISE * w
    inner = geom.pointed_arc(w, spring, SEG)
    outer = geom.pointed_arc(w, spring, SEG, grow=RR_SURROUND)
    hw = w / 2.0
    zs, zl = RR_PROUD, 0.1  # the surround's face; the leaves' face
    # The surround: jambs and the arched band, front face and inner reveal.
    ring_in = [(-hw, 0.0)] + inner + [(hw, 0.0)]
    ring_out = [(-hw - RR_SURROUND, 0.0)] + outer + [(hw + RR_SURROUND, 0.0)]
    for (a, b), (c, d) in zip(zip(ring_in, ring_in[1:]), zip(ring_out, ring_out[1:])):
        _poly(m, [a, b, d, c], zs, STONE_WARM, "stone")
        q = [(a[0], a[1], zl), (b[0], b[1], zl), (b[0], b[1], zs), (a[0], a[1], zs)]
        n = geom.newell(q)
        mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        if n[0] * (0.0 - mid[0]) + n[1] * (spring - mid[1]) < 0:
            q.reverse()
        m.poly(q, "stone", zone=STONE_WARM)
        q = [(c[0], c[1], 0.0), (d[0], d[1], 0.0), (d[0], d[1], zs), (c[0], c[1], zs)]
        n = geom.newell(q)
        mid = ((c[0] + d[0]) / 2, (c[1] + d[1]) / 2)
        if n[0] * (mid[0] - 0.0) + n[1] * (mid[1] - spring) < 0:
            q.reverse()
        m.poly(q, "stone", zone=STONE_WARM)
    # The leaves: two halves of the pointed outline, a dark seam between.
    left = [(-hw, 0.0), (-0.012, 0.0)] + [(min(x, -0.012), y) for x, y in reversed(inner[:SEG + 1])]
    right = [(0.012, 0.0), (hw, 0.0)] + [(max(x, 0.012), y) for x, y in reversed(inner[SEG:])]
    _poly(m, left, zl, OAK_AGED, "wood")
    _poly(m, right, zl, OAK_AGED, "wood")
    _poly(m, [(-0.012, 0.0), (0.012, 0.0), (0.012, apex - 0.02), (-0.012, apex - 0.02)], zl - 0.01, SOOT, "stone")
    for y in (0.7, 2.0, 3.2):
        for sx in (-1.0, 1.0):
            x0, x1 = sorted((sx * 0.05, sx * (hw - 0.08)))
            mk.box(m, (x0, y - 0.05, zl), (x1, y + 0.05, zl + 0.025), "metal", IRON, skip=("-z",))
            fx = sx * 0.12
            _lozenge(m, fx, y, 0.07, 0.11, zl + 0.032, IRON, "metal")
    for sx in (-1.0, 1.0):
        # A ring pull hanging flat on the leaf from a boss: four bars of a square ring.
        rx, ry, r, t = sx * 0.3, 1.2, 0.1, 0.022
        mk.box(m, (rx - 0.04, ry + r + 0.02, zl), (rx + 0.04, ry + r + 0.1, zl + 0.04), "metal", BRASS, skip=("-z",))
        for x0, y0, x1, y1 in ((rx - r, ry - r, rx + r, ry - r + t), (rx - r, ry + r - t, rx + r, ry + r),
                               (rx - r, ry - r + t, rx - r + t, ry + r - t), (rx + r - t, ry - r + t, rx + r, ry + r - t)):
            mk.box(m, (x0, y0, zl + 0.005), (x1, y1, zl + 0.03), "metal", BRASS, skip=("-z",))
    p.meta.update(width=w + 2 * RR_SURROUND, height=apex, proud=zs)
    return _finish(p, m)


# =====================================================================================
# The passages' own pieces: the sloping ceiling over a stair, rubble
# =====================================================================================

PASSAGE_CLEAR = 3.5  # the passages' ceiling over a landing (the layout) and over a stair's treads
BEAM_PITCH = 1.6  # the timbers under a sloping ceiling: at most this apart
BEAM = (0.2, 0.18)  # a timber's width along the stair, its depth below the ceiling


def passage_ceiling(width: float = 2.0, length: float = 16.0, rise: float = 9.0, plane: float = 1) -> Piece:
    """`kit_passage_ceiling`: the low ceiling over a passage stair: a plane
    PASSAGE_CLEAR over the ramp from the stair's foot (z 0) to its top (z
    `length`, `rise` up), across its `width` (x -w/2..w/2), in the cell's
    rough stone (world UVs), carried on rough oak timbers every BEAM_PITCH or
    less, each on a stone corbel at both walls. The layout's ceiling over a
    stair is flat at the top landing's height, so without it the stair would
    rise in a tall slot; the plane is also the camera's (meta "collider").
    `plane` 0 (a landing, `rise` 0): the timbers and corbels alone, under the
    layout's own flat ceiling. Origin: the stair's foot at its centre line,
    +z up the stair."""
    p = Piece("")
    m = mk.Mesh()
    hw = width / 2.0
    k = rise / length

    def yc(z):
        return PASSAGE_CLEAR + k * z

    quad = [(-hw, yc(0.0), 0.0), (-hw, yc(length), length), (hw, yc(length), length), (hw, yc(0.0), 0.0)]
    if int(round(plane)):
        add_face(p.faces, quad, WALL, None, (0.0, -1.0, k))
    n = max(2, math.ceil(length / BEAM_PITCH))
    bw, bd = BEAM
    c = 0.05  # the timbers' chamfer
    for i in range(1, n):
        z = length * i / n
        za, zb = z - bw / 2, z + bw / 2
        ya, yb = yc(za), yc(zb)
        # Front, back, the chamfered underside (three faces).
        m.poly([(-hw, ya - bd + c, za), (hw, ya - bd + c, za), (hw, ya, za), (-hw, ya, za)][::-1], "wood", zone=OAK_AGED)
        m.poly([(-hw, yb - bd + c, zb), (hw, yb - bd + c, zb), (hw, yb, zb), (-hw, yb, zb)], "wood", zone=OAK_AGED)
        a0, a1 = (za + c, ya - bd + k * c), (zb - c, yb - bd - k * c)
        m.poly([(-hw, ya - bd + c, za), (-hw, a0[1], a0[0]), (hw, a0[1], a0[0]), (hw, ya - bd + c, za)][::-1], "wood",
               zone=OAK_AGED)
        m.poly([(-hw, a0[1], a0[0]), (-hw, a1[1], a1[0]), (hw, a1[1], a1[0]), (hw, a0[1], a0[0])][::-1], "wood",
               zone=OAK_AGED)
        m.poly([(-hw, a1[1], a1[0]), (-hw, yb - bd + c, zb), (hw, yb - bd + c, zb), (hw, a1[1], a1[0])][::-1], "wood",
               zone=OAK_AGED)
        # A stone corbel under each end, against the wall.
        for sx in (-1.0, 1.0):
            x0, x1 = sorted((sx * hw, sx * (hw - 0.22)))
            y_top = min(ya, yb) - bd
            mk.box(m, (x0, y_top - 0.32, za - 0.05), (x1, y_top - 0.004, zb + 0.05), "stone", STONE_DUNGEON,
                   skip=("-x" if sx < 0 else "+x",))
    p.meta.update(collider=[quad] if int(round(plane)) else [], clear=PASSAGE_CLEAR, beams=n - 1)
    return _finish(p, m)


def rubble(seed: float = 1, spread: float = 0.9) -> Piece:
    """`kit_rubble`: a few fallen stones and a broken slab heaped in a
    passage's corner (the props atlas's stone in the dungeon tone), at most
    0.35 m high within `spread` of its origin (a floor piece); every stone's
    top at its own height. Walk-through."""
    rng = random.Random(int(round(seed)) * 7919 + 17)
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    tops = [0.12 + 0.03 * i for i in range(7)]
    rng.shuffle(tops)
    for top in tops:
        x, z = rng.uniform(-spread, spread) * 0.8, rng.uniform(0.0, spread)
        s = rng.uniform(0.1, 0.22)
        mark = m.mark()
        mk.box(m, (-s, 0.0, -s * 0.8), (s, top, s * 0.8), "stone", STONE_DUNGEON, skip=("-y",))
        m.place(mark, rng.uniform(0, math.pi), (x, 0.0, z))
    mark = m.mark()
    mk.box(m, (-0.45, 0.0, -0.25), (0.45, 0.07, 0.25), "stone", STONE_DUNGEON, skip=("-y",))
    m.place(mark, rng.uniform(-0.4, 0.4), (0.0, 0.0, spread * 0.4))
    p.meta.update(height=0.35)
    return _finish(p, m)


def _rough_block(faces, cx, cy, w, h, d, bevel=0.035):
    """A rough wall block in the cell's own wall stone (world UVs, so its courses run on
    from the wall): straight sides to 0.6 d, then a bevel in to its face at d."""
    x0, x1, y0, y1 = cx - w / 2, cx + w / 2, cy - h / 2, cy + h / 2
    zm = 0.6 * d
    back = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    face = [(x0 + bevel, y0 + bevel), (x1 - bevel, y0 + bevel), (x1 - bevel, y1 - bevel), (x0 + bevel, y1 - bevel)]
    for i in range(4):
        (ax, ay), (bx, by) = back[i], back[(i + 1) % 4]
        (fx, fy), (gx, gy) = face[i], face[(i + 1) % 4]
        q = [(ax, ay, 0.0), (bx, by, 0.0), (bx, by, zm), (ax, ay, zm)]
        add_face(faces, q, WALL, None, geom.newell(q))
        q = [(ax, ay, zm), (bx, by, zm), (gx, gy, d), (fx, fy, d)]
        add_face(faces, q, WALL, None, geom.newell(q))
    add_face(faces, [(x, y, d) for x, y in face], WALL, None, (0.0, 0.0, 1.0))


ROUGH_COURSE = (0.62, 0.42)  # the rough stones' grid: along the wall, up it
ROUGH_PROUD = (0.025, 0.06)


def rough_stones(length: float = 8.0, height: float = 3.2, rise: float = 0.0, seed: float = 1, share: float = 0.5) -> Piece:
    """`kit_rough_stones`: rough blocks standing a few centimetres proud of a
    passage wall, in the cell's own wall stone (world UVs), so the wall
    reads as rough rubble walling: about `share` of the cells of a
    ROUGH_COURSE grid over `length` x `height` (from 0.25 m over the floor;
    `rise` lifts the grid along a stair's wall from x = -length/2 to
    +length/2), each jittered in its cell, never overlapping. A wall piece:
    origin at the span's centre on the wall face, at the floor's height at
    its x = -length/2 end. Seeded."""
    rng = random.Random(int(round(seed)) * 3571 + 5)
    p = Piece("")
    cw, ch = ROUGH_COURSE
    cols, rows = max(1, int(length // cw)), max(1, int((height - 0.25) // ch))
    x0 = -length / 2.0 + (length - cols * cw) / 2.0
    for i in range(cols):
        for j in range(rows):
            if rng.random() > share:
                continue
            w, h = rng.uniform(0.3, cw - 0.12), rng.uniform(0.18, ch - 0.1)
            cx = x0 + (i + 0.5) * cw + rng.uniform(-0.04, 0.04)
            cy = 0.25 + (j + 0.5) * ch + rise * (cx + length / 2.0) / length
            d = rng.choice((ROUGH_PROUD[0], 0.04, ROUGH_PROUD[1]))
            _rough_block(p.faces, cx, cy, w, h, d)
    p.meta.update(proud=ROUGH_PROUD[1])
    return p


# =====================================================================================
# The Lost Property Vault: the mound and the stacks of unclaimed oddments
# =====================================================================================

BOOKS = (BOOK_RED, BOOK_GREEN, BOOK_BLUE, BOOK_BROWN, BOOK_OCHRE)
CLOTHS = (HOUSE_A, HOUSE_B, HOUSE_C, HOUSE_D, WINE, NAVY, CREAM, CANVAS)
METALS = (GILT, BRASS, PEWTER, COPPER, BRONZE, IRON)


def _crate(m, rng, x, y, z, s, yaw):
    mark = m.mark()
    mk.box(m, (-s / 2, 0.0, -s / 2), (s / 2, s * 0.85, s / 2), "wood", rng.choice((OAK, OAK_AGED)), skip=("-y",))
    for yy in (0.08, s * 0.85 - 0.08):
        mk.box(m, (-s / 2 - 0.01, yy - 0.03, -s / 2 - 0.01), (s / 2 + 0.01, yy + 0.03, s / 2 + 0.01), "wood", OAK_AGED,
               skip=("-y", "+y"))
    m.place(mark, yaw, (x, y, z))
    return s * 0.85


def _trunk(m, rng, x, y, z, s, yaw):
    mark = m.mark()
    w, d, h = s * 1.4, s * 0.8, s * 0.7
    mk.box(m, (-w / 2, 0.0, -d / 2), (w / 2, h, d / 2), "leather", rng.choice((LEATHER, BOOK_BROWN, BOOK_RED)), skip=("-y",))
    for xx in (-w / 2 + 0.12, w / 2 - 0.12):
        mk.box(m, (xx - 0.03, -0.005, -d / 2 - 0.01), (xx + 0.03, h + 0.01, d / 2 + 0.01), "metal", BRASS, skip=("-y",))
    m.place(mark, yaw, (x, y, z))
    return h


def _books(m, rng, x, y, z, yaw):
    mark = m.mark()
    yy = 0.0
    for _ in range(rng.randint(3, 6)):
        w, d, h = rng.uniform(0.22, 0.32), rng.uniform(0.16, 0.22), rng.uniform(0.04, 0.07)
        a = rng.uniform(-0.3, 0.3)
        sub = m.mark()
        mk.box(m, (-w / 2, 0.0, -d / 2), (w / 2, h, d / 2), "leather", rng.choice(BOOKS), skip=("-y",))
        m.place(sub, a, (0.0, yy, 0.0))
        yy += h
    m.place(mark, yaw, (x, y, z))
    return yy


def _chair(m, rng, x, y, z, yaw, upside=False):
    mark = m.mark()
    zone = rng.choice((OAK, OAK_AGED))
    mk.box(m, (-0.22, 0.42, -0.22), (0.22, 0.47, 0.22), "wood", zone)
    for sx in (-0.19, 0.19):
        for sz in (-0.19, 0.19):
            mk.box(m, (sx - 0.025, 0.0, sz - 0.025), (sx + 0.025, 0.42, sz + 0.025), "wood", zone, skip=("-y", "+y"))
    mk.box(m, (-0.22, 0.47, -0.22), (0.22, 0.95, -0.18), "wood", zone)
    if upside:
        m.transform(mark, lambda p: (p[0], 0.95 - p[1], p[2]))
        for f in m.faces[mark[1]:]:
            f.idx = tuple(reversed(f.idx))
            f.uvs = list(reversed(f.uvs))
    m.place(mark, yaw, (x, y, z))
    return 0.95


def _lamp(m, rng, x, y, z):
    mark = m.mark()
    zone = rng.choice(METALS)
    mk.lathe(m, [(0.1, 0.0), (0.1, 0.03), (0.025, 0.06), (0.025, 0.5), (0.12, 0.62), (0.05, 0.75), (0.0, 0.78)], 6, "metal",
             zone, cap_top=False)
    m.place(mark, 0.0, (x, y, z))
    return 0.78


def _globe(m, rng, x, y, z):
    mark = m.mark()
    mk.lathe(m, [(0.12, 0.0), (0.12, 0.03), (0.03, 0.05), (0.03, 0.2)], 6, "metal", BRASS, cap_top=False)
    mk.lathe(m, [(0.0, 0.2), (0.15, 0.27), (0.2, 0.4), (0.15, 0.53), (0.0, 0.6)], 8, "glass", rng.choice((SKY, FOREST)),
             cap_top=False)
    m.place(mark, 0.0, (x, y, z))
    return 0.6


def _cloth(m, rng, x, y, z, yaw, w):
    """A draped dust sheet or banner over the heap's shoulder: a folded band."""
    mark = m.mark()
    zone = rng.choice(CLOTHS)
    pts = [(-w / 2, 0.0), (-w / 4, 0.25), (0.0, 0.1), (w / 4, 0.3), (w / 2, 0.05)]
    for (xa, ya), (xb, yb) in zip(pts, pts[1:]):
        m.poly([(xa, ya, -0.3), (xb, yb, -0.3), (xb, yb + 0.02, 0.35), (xa, ya + 0.02, 0.35)], "fabric", zone=zone)
    m.place(mark, yaw, (x, y, z))


def lost_heap(seed: float = 1, radius: float = 2.6) -> Piece:
    """`kit_lost_heap`: the Mound of Unclaimed Oddments at the Lost Property
    Vault's find spot: a mound of crates, trunks, chairs (some upside down),
    stacks of books, lamps, globes and draped cloths in tiers, about
    `radius` across and up to about 3 m high; its top is clear of the find
    spot's middle (the heap stands behind it, origin at the heap's front
    centre, the heap rising toward -z). Walk-through. Seeded."""
    rng = random.Random(int(round(seed)) * 104729 + 3)
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    tiers = ((radius, 0.0), (radius * 0.72, 0.8), (radius * 0.45, 1.6), (radius * 0.22, 2.3))
    for r, y in tiers:
        n = max(3, int(r * 5.5))
        for i in range(n):
            a = math.pi * (0.05 + 0.9 * i / max(1, n - 1)) + rng.uniform(-0.1, 0.1)
            x, z = math.cos(a) * r * rng.uniform(0.6, 1.0), -0.5 - math.sin(a) * r * rng.uniform(0.4, 0.9)
            k = rng.random()
            if k < 0.32:
                _crate(m, rng, x, y, z, rng.uniform(0.5, 0.8), rng.uniform(0, math.pi))
            elif k < 0.52:
                _trunk(m, rng, x, y, z, rng.uniform(0.6, 0.85), rng.uniform(0, math.pi))
            elif k < 0.67:
                _chair(m, rng, x, y, z, rng.uniform(0, math.tau), upside=rng.random() < 0.5)
            elif k < 0.82:
                _books(m, rng, x, y, z, rng.uniform(0, math.pi))
            elif k < 0.9:
                _lamp(m, rng, x, y, z)
            else:
                _globe(m, rng, x, y, z)
        _cloth(m, rng, rng.uniform(-r * 0.5, r * 0.5), y + 0.5, -0.5 - r * 0.5, rng.uniform(-0.5, 0.5), 1.4)
    p.meta.update(radius=radius, height=3.0)
    return _finish(p, m)


def lost_stack(seed: float = 1, height: float = 5.0) -> Piece:
    """`kit_lost_stack`: a tall tottering column of unclaimed oddments on a 1.6 m
    square (crates, trunks, chairs, books, a lamp on top), up to `height`,
    each layer turned and shifted a little. Walk-through. Seeded."""
    rng = random.Random(int(round(seed)) * 6151 + 11)
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    y = 0.0
    while y < height - 0.6:
        k = rng.random()
        x, z = rng.uniform(-0.15, 0.15), rng.uniform(-0.15, 0.15)
        yaw = rng.uniform(-0.4, 0.4)
        if k < 0.4:
            y += _crate(m, rng, x, y, z, rng.uniform(0.9, 1.3), yaw)
        elif k < 0.65:
            y += _trunk(m, rng, x, y, z, rng.uniform(0.8, 1.0), yaw)
        elif k < 0.8:
            _chair(m, rng, x - 0.25, y, z, yaw)
            _chair(m, rng, x + 0.3, y, z + 0.1, yaw + math.pi, upside=True)
            y += 0.95
        else:
            h = 0.0
            for dx in (-0.3, 0.3):
                h = max(h, _books(m, rng, x + dx, y, z, yaw))
            y += h
    _lamp(m, rng, 0.0, y, 0.0)
    p.meta.update(height=round(y + 0.78, 3))
    return _finish(p, m)


# =====================================================================================
# The Gathering Room: floor cushions
# =====================================================================================

CUSHION = (0.7, 0.16)  # a floor cushion: side, height


def floor_cushion(cover: float = 0) -> Piece:
    """`kit_floor_cushion`: a square floor cushion (house_a..d by `cover`
    0-3, 4 the tapestry wine) with a piped edge and a button; one floor seat
    on its top (meta "floor_seats"). Walk-through."""
    i = int(round(cover))
    zones = (HOUSE_A, HOUSE_B, HOUSE_C, HOUSE_D, WINE)
    if not 0 <= i < len(zones):
        raise ValueError("kit_floor_cushion: cover 0..4")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    s, h = CUSHION
    hs = s / 2.0
    mk.lathe(m, [(hs * 1.3, 0.0), (hs * 1.38, h * 0.5), (hs * 1.2, h)], 4, "fabric", zones[i], phase=math.pi / 4,
             smooth=False, cap_top=True)
    mk.lathe(m, [(0.035, h - 0.005), (0.0, h + 0.012)], 6, "metal", BRASS, cap_top=False)
    p.meta.update(height=h, floor_seats=[[-hs + 0.05, -hs + 0.05, hs - 0.05, hs - 0.05, h]])
    return _finish(p, m)


# =====================================================================================
# Under the covered bridge (kit_ravine)
# =====================================================================================

RAVINE_STEP = (2.0, 1.4)  # facet grid: along x, along z
RAVINE_LIFT = (0.12, 0.45)  # the rocky ground over the backdrop's ground: least, most
RAVINE_PILE = 0.35  # the rubble piled against the courtyard's wall foot
RAVINE_SKIRT = -16.0  # its ends' skirts reach under the lake (y -15)
BRIDGE_DECK = (101.0, 107.0, 74.0, 92.0, 2.06)  # the covered bridge's deck: x0, x1, z0, z1, rise (layout)


def ravine(x0: float = 48.0, x1: float = 112.0, z0: float = 75.02, z1: float = 91.7, seed: float = 1) -> Piece:
    """`kit_ravine`: rocky ground under the covered bridge (the 3c leftover:
    from the grounds the backdrop's flat grey crag shelf showed under the
    deck): flat-shaded rock facets (the props atlas's rough tile in the
    crag's and the grounds' rock colours) lying RAVINE_LIFT over the
    backdrop's own ground from the Clock Tower Courtyard's outer wall foot
    (z `z0`, rubble piled RAVINE_PILE against it) to just under the grounds'
    ground at its north edge (z `z1`), over x `x0`..`x1`, kept 0.3 m under
    the bridge's deck, with skirts down under the lake at both ends.
    Nothing walks there (no layout surface). World coordinates: placed at
    the origin, yaw 0. Seeded, deterministic."""
    import hlod_crag

    from castle_kit import schema as S

    ground, _shelf = hlod_crag.backdrop_ground(os.path.join(os.path.dirname(S.LAYOUT_PATH), "terrain"))
    rng = random.Random(int(round(seed)) * 911 + 7)
    nx = max(1, int(round((x1 - x0) / RAVINE_STEP[0])))
    nz = max(2, int(round((z1 - z0) / RAVINE_STEP[1])))
    dx0, dx1, dz0, dz1, rise = BRIDGE_DECK
    grid = []
    for j in range(nz + 1):
        z = z0 + (z1 - z0) * j / nz
        row = []
        for i in range(nx + 1):
            x = x0 + (x1 - x0) * i / nx
            zz = z
            if 0 < j < nz and 0 < i < nx:  # the edges stay straight (the ends' skirts hang from them)
                x += rng.uniform(-0.4, 0.4)
                zz += rng.uniform(-0.3, 0.3)
            if j == 0:
                y = ground(x, zz) + RAVINE_PILE
            elif j == nz:
                y = ground(x, zz) - 0.05
            else:
                y = ground(x, zz) + rng.uniform(*RAVINE_LIFT)
            if dx0 - 2.5 <= x <= dx1 + 2.5:
                y = min(y, rise * (zz - dz0) / (dz1 - dz0) - 0.35)
            row.append((x, y, zz))
        grid.append(row)
    m = mk.Mesh()
    for j in range(nz):
        for i in range(nx):
            a, b, c, d = grid[j][i], grid[j][i + 1], grid[j + 1][i + 1], grid[j + 1][i]
            zone = ROCK if (i + j) % 3 else ROCK_LIGHT
            for tri in ((a, d, c), (a, c, b)) if (i + j) % 2 else ((a, d, b), (b, d, c)):
                n = geom.newell(tri)
                m.poly(list(tri) if n[1] > 0 else list(reversed(tri)), "leather", zone=zone)
    for i_end, sx in ((0, -1.0), (nx, 1.0)):
        col = [grid[j][i_end] for j in range(nz + 1)]
        for p0, p1 in zip(col, col[1:]):
            q = [p0, p1, (p1[0], RAVINE_SKIRT, p1[2]), (p0[0], RAVINE_SKIRT, p0[2])]
            if geom.newell(q)[0] * sx < 0:
                q.reverse()
            m.poly(q, "leather", zone=ROCK)
    p = Piece("", walkthrough=True)
    p.meta.update(rect=(x0, z0, x1, z1))
    return _finish(p, m)


BUILDERS = {
    "kit_entrance_tapestry": entrance_tapestry,
    "kit_entrance_panel": entrance_panel,
    "kit_entrance_painting": entrance_painting,
    "kit_entrance_statue": entrance_statue,
    "kit_entrance_wall": entrance_wall,
    "kit_wishing_room_door": wishing_room_door,
    "kit_passage_ceiling": passage_ceiling,
    "kit_rubble": rubble,
    "kit_rough_stones": rough_stones,
    "kit_lost_heap": lost_heap,
    "kit_lost_stack": lost_stack,
    "kit_floor_cushion": floor_cushion,
    "kit_ravine": ravine,
}
CATALOGUE = {
    "kit_entrance_tapestry": {"subject": 0},
    "kit_entrance_panel": {"case": 1},
    "kit_entrance_painting": {"subject": 0},
    "kit_entrance_statue": {},
    "kit_entrance_wall": {"stone": 1},
    "kit_wishing_room_door": {},
    "kit_passage_ceiling": {"width": 2.0, "length": 16.0, "rise": 9.0},
    "kit_rubble": {"seed": 1},
    "kit_rough_stones": {"length": 8.0, "seed": 1},
    "kit_lost_heap": {"seed": 1},
    "kit_lost_stack": {"seed": 1, "height": 5.0},
    "kit_floor_cushion": {"cover": 0},
    "kit_ravine": {},
}
