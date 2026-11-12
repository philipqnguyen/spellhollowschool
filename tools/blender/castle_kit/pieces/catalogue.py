"""The Owl Order Catalogue's stand (docs/architecture.md "Economy and crafting";
docs/art/castle-kit.md): one in each house common
room, where players browse and order the owl-order catalogue (the layout's
`owl_order` interactables stand about a metre in front of it).

`kit_catalogue_stand`: an oak lectern-cabinet holding the large open
catalogue on its sloped reading desk, a ribbon bookmark in the house's
colour hanging over the desk's lip and house-coloured panels on the
cabinet's sides; under the desk, open shelves of order forms on a brass tray
and parcels waiting for the owls; beside it (`perch` 1 right, -1 left, 0
none) a T-perch with a delivery owl.

Frame as props.json `kit`: origin at the bottom centre of the cabinet's
plinth, yaw 0 facing +z (the reader stands in front, at +z). Players walk
through it like the rest of the room furniture (`walkthrough`); its floor
footprint is `meta["footprint"]` [x0, z0, x1, z1] in the piece's frame (the
cabinet and the perch's foot), for anything that wants to keep characters
out of it.

Everything is our own invented design. Colours: palette names only
(grid.palette_tag; props slots castle_textures.json already holds: oak,
oak_aged, brass, the book leathers, letter_white, parcel_tan, owl_brown,
owl_pale, house_a..d); pages on the untinted parchment tile, print on the
untinted dark tile; no colour literal here."""

from __future__ import annotations

import math

import kit_models as km
import meshkit as mk
from castle_kit.pieces.geom import Piece
from castle_kit.pieces.hall import from_mesh

# Zone -> palette name (castle_textures.json `palette`; each [tile, "@name"] a props slot).
OAK, OAK_AGED, BRASS, LEATHER, SWATCH_RED, SWATCH_GREEN, FORM, PARCEL, HOUSE0 = 1, 2, 3, 4, 5, 6, 7, 8, 20
OWL, OWL_FACE, BEAK = 11, 12, 90  # kit_models._owl's zones (beak: 90)
ZONES = {OAK: "oak", OAK_AGED: "oak_aged", BRASS: "brass", LEATHER: "book_brown", SWATCH_RED: "book_red",
         SWATCH_GREEN: "ledger_green", FORM: "letter_white", PARCEL: "parcel_tan", OWL: "owl_brown",
         OWL_FACE: "owl_pale", BEAK: "brass",
         **{HOUSE0 + i: f"house_{x}" for i, x in enumerate("abcd")}}

CATALOGUE_STAND = "kit_catalogue_stand"
CABINET = (0.30, 0.20, 0.92)  # half width, half depth, top of the cabinet under the desk
PLINTH = (0.34, 0.24, 0.08)  # half width, half depth, height
DESK = (0.40, 0.28, 1.05, math.radians(20.0))  # half width, half depth along its slope, centre height, slope
DESK_Z = 0.02  # the desk's centre ahead of the cabinet's
PERCH_X = 0.62  # the perch's post off the cabinet's centre line
PERCH_H = 1.35  # the perch's bar
STAND_TRIS = 1800


def _desk_frame(m: mk.Mesh, mark) -> None:
    """Moves geometry built in the desk's frame (x across, y off the board's
    top, z down its slope toward the reader) onto the sloped desk."""
    _, _, cy, a = DESK
    c, s = math.cos(a), math.sin(a)

    def to_world(p):
        x, v, w = p
        return (x, cy + v * c - w * s, DESK_Z + v * s + w * c)

    m.transform(mark, to_world)


def _quad2(m: mk.Mesh, pts, tile: str, zone: int) -> None:
    """A thin strip seen from both sides."""
    m.poly(pts, tile, zone=zone)
    m.poly(list(reversed(pts)), tile, zone=zone)


def _cabinet(m: mk.Mesh, house: int) -> None:
    hw, hd, top = CABINET
    pw, pd, ph = PLINTH
    mk.box(m, (-pw, 0.0, -pd), (pw, ph, pd), "wood", OAK_AGED, skip=("-y",))
    mk.box(m, (-pw - 0.02, ph, -pd - 0.02), (pw + 0.02, ph + 0.025, pd + 0.02), "wood", OAK_AGED, skip=("-y",))
    y0 = ph + 0.025
    # Sides, back and shelves (open toward the reader).
    for sx in (-1.0, 1.0):
        mk.box(m, (min(sx * hw, sx * (hw - 0.03)), y0, -hd), (max(sx * hw, sx * (hw - 0.03)), top, hd), "wood", OAK,
               skip=("-y",))
        # The house panel on the side's outer face.
        xo = sx * (hw + 0.003)
        pts = [(xo, y0 + 0.16, -0.13), (xo, y0 + 0.16, 0.13), (xo, top - 0.12, 0.13), (xo, top - 0.12, -0.13)]
        m.poly(pts if mk.newell(pts)[0] * sx > 0 else list(reversed(pts)), "fabric", zone=HOUSE0 + house)
    mk.box(m, (-hw + 0.03, y0, -hd), (hw - 0.03, top, -hd + 0.03), "wood", OAK, skip=("-y", "-z"))
    for y in (y0, 0.47, top - 0.03):
        mk.box(m, (-hw + 0.03, y, -hd + 0.03), (hw - 0.03, y + 0.03, hd), "wood", OAK, skip=("-y",) if y == y0 else ())
    # The lower shelf: parcels for the owls, string round them.
    for x, w, h, d in ((-0.14, 0.2, 0.16, 0.26), (0.1, 0.24, 0.12, 0.3), (0.12, 0.16, 0.1, 0.2)):
        yb = y0 + 0.03 if h != 0.1 else y0 + 0.03 + 0.12
        mk.box(m, (x - w / 2, yb, -d / 2), (x + w / 2, yb + h, d / 2), "fabric", PARCEL, skip=("-y",))
    # The middle shelf: a brass tray of order forms and two stacks beside it.
    ys = 0.50
    mk.box(m, (-0.25, ys, -0.13), (-0.01, ys + 0.02, 0.15), "metal", BRASS, skip=("-y",))
    mk.box(m, (-0.235, ys + 0.02, -0.11), (-0.025, ys + 0.05, 0.13), "fabric", FORM, skip=("-y",))
    for x, h in ((0.08, 0.07), (0.19, 0.11)):
        mk.box(m, (x - 0.05, ys, -0.1), (x + 0.05, ys + h, 0.12), "fabric", FORM, skip=("-y",))
    # Cheeks and back carrying the sloped desk over the cabinet's top.
    _, dd, cy, a = DESK
    c, s = math.cos(a), math.sin(a)

    def under(z):  # the board's underside over the cabinet at depth z
        w = (z - DESK_Z) / c
        return cy - 0.04 * c - w * s - 0.002

    for sx in (-1.0, 1.0):
        x = sx * hw
        pts = [(x, top, -hd), (x, top, hd), (x, under(hd), hd), (x, under(-hd), -hd)]
        m.poly(pts if mk.newell(pts)[0] * sx > 0 else list(reversed(pts)), "wood", zone=OAK)
    back = [(hw, top, -hd), (-hw, top, -hd), (-hw, under(-hd), -hd), (hw, under(-hd), -hd)]
    m.poly(back if mk.newell(back)[2] < 0 else list(reversed(back)), "wood", zone=OAK)
    front = [(-hw, top, hd), (hw, top, hd), (hw, under(hd), hd), (-hw, under(hd), hd)]
    m.poly(front if mk.newell(front)[2] > 0 else list(reversed(front)), "wood", zone=OAK)
    # A brass name plate on the cabinet's top rail.
    mk.box(m, (-0.09, top - 0.075, hd), (0.09, top - 0.02, hd + 0.008), "metal", BRASS, skip=("-z",))


def _pages(m: mk.Mesh, sx: float, house: int) -> None:
    """One half of the open catalogue (sx -1 left, 1 right) in the desk frame:
    the page block rising from the spine, ruled lines and two pictures."""
    outline = [(0.012, 0.012), (0.30, 0.012), (0.30, 0.04), (0.03, 0.04), (0.012, 0.028)]
    if sx < 0:
        outline = [(-x, y) for x, y in reversed(outline)]
    mk.prism(m, outline, -0.19, 0.19, "parchment", 0, back=True)
    # Print: lines of text and two coloured plates (things for sale).
    y = 0.0445  # 4.5 mm over the page (the lint's z-fighting planes are 2 mm bins)
    for k in range(7):
        w = -0.17 + k * 0.05
        x0, x1 = (0.06, 0.27) if k not in (1, 4) else (0.15, 0.27)
        a, b = sorted((sx * x0, sx * x1))
        m.poly([(a, y, w), (b, y, w), (b, y, w - 0.008), (a, y, w - 0.008)], "dark", zone=0)
    for (x0, x1, w0, w1), zone in (((0.06, 0.13, -0.15, -0.09), SWATCH_RED if sx < 0 else SWATCH_GREEN),
                                     ((0.06, 0.13, -0.0, 0.06), HOUSE0 + house if sx < 0 else PARCEL)):
        a, b = sorted((sx * x0, sx * x1))
        tile = "leather" if zone in (SWATCH_RED, SWATCH_GREEN) else "fabric"
        m.poly([(a, y, w1), (b, y, w1), (b, y, w0), (a, y, w0)], tile, zone=zone)


def _desk(m: mk.Mesh, house: int) -> None:
    hw, hd, _, _ = DESK
    mark = m.mark()
    # The board (its top at y 0 of the desk frame) and the lip that keeps the book on it.
    mk.box(m, (-hw, -0.04, -hd), (hw, 0.0, hd), "wood", OAK)
    mk.box(m, (-hw, 0.0, hd - 0.025), (hw, 0.03, hd), "wood", OAK_AGED, skip=("-y",))
    # The catalogue: leather covers, the page blocks, the spine's fold.
    mk.box(m, (-0.32, 0.0, -0.205), (0.32, 0.012, 0.205), "leather", LEATHER, skip=("-y",))
    for sx in (-1.0, 1.0):
        _pages(m, sx, house)
    # The ribbon bookmark along the right page to the lip.
    rx = 0.05
    _quad2(m, [(rx, 0.05, -0.12), (rx + 0.022, 0.05, -0.12), (rx + 0.022, 0.05, 0.21), (rx, 0.05, 0.21)],
           "fabric", HOUSE0 + house)
    _desk_frame(m, mark)
    # ... and hanging over the lip.
    _, _, cy, a = DESK
    c, s = math.cos(a), math.sin(a)
    top = (cy + 0.035 * c - (hd + 0.005) * s, DESK_Z + 0.035 * s + (hd + 0.005) * c)
    _quad2(m, [(rx, top[0] - 0.2, top[1]), (rx + 0.022, top[0] - 0.2, top[1]), (rx + 0.022, top[0], top[1]),
               (rx, top[0], top[1])], "fabric", HOUSE0 + house)
    # An inkwell and a quill on the board's upper corner.
    ink = m.mark()
    mk.lathe(m, [(0.035, 0.0), (0.035, 0.03), (0.015, 0.045), (0.015, 0.055)], 6, "metal", BRASS, cap_top=True)
    mk.rod(m, (0.0, 0.05, 0.0), (0.06, 0.2, -0.05), 0.004, 3, "metal", BRASS)
    _quad2(m, [(0.02, 0.1, -0.017), (0.03, 0.1, -0.025), (0.072, 0.23, -0.06), (0.058, 0.215, -0.048)], "fabric", FORM)
    m.transform(ink, lambda p: (p[0] + 0.355, p[1], p[2] - 0.2))
    _desk_frame(m, ink)


def _perch(m: mk.Mesh, x: float) -> None:
    mark = m.mark()
    mk.lathe(m, [(0.0, 0.0), (0.18, 0.0), (0.18, 0.04), (0.12, 0.07), (0.04, 0.1)], 8, "wood", OAK_AGED,
             cap_bottom=False)
    mk.lathe(m, [(0.028, 0.1), (0.024, PERCH_H)], 6, "wood", OAK_AGED, cap_top=False)
    mk.rod(m, (0.0, PERCH_H, -0.2), (0.0, PERCH_H, 0.2), 0.02, 6, "wood", OAK_AGED)
    for sz in (-1.0, 1.0):
        mk.lathe(m, [(0.0, -0.025), (0.03, 0.0), (0.0, 0.025)], 6, "metal", BRASS, (0.0, PERCH_H, sz * 0.21))
    # The delivery owl, on the bar, facing the reader; a letter in its beak's reach.
    km._owl(m, (0.0, PERCH_H + 0.02, 0.06), 0.0, OWL, OWL_FACE, s=1.6)
    mk.box(m, (-0.05, PERCH_H + 0.27, 0.16), (0.05, PERCH_H + 0.335, 0.168), "fabric", FORM)
    m.transform(mark, lambda p: (p[0] + x, p[1], p[2]))


def catalogue_stand(house: float = 0, perch: float = 1) -> Piece:
    """`kit_catalogue_stand`: the Owl Order Catalogue on its stand (module
    docstring); `house` 0-3 colours the bookmark and the side panels
    (house_a..d), `perch` 1 / -1 / 0 puts the owl's perch right, left or
    nowhere. Walk-through; at most STAND_TRIS triangles."""
    h, side = int(round(house)), int(round(perch))
    if not 0 <= h <= 3:
        raise ValueError("kit_catalogue_stand: house 0..3")
    if side not in (-1, 0, 1):
        raise ValueError("kit_catalogue_stand: perch -1, 0 or 1")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    _cabinet(m, h)
    _desk(m, h)
    pw, pd, _ = PLINTH
    x0, x1 = -pw - 0.02, pw + 0.02
    if side:
        _perch(m, side * PERCH_X)
        x0, x1 = min(x0, side * PERCH_X - 0.18), max(x1, side * PERCH_X + 0.18)
    a = DESK[3]
    _finish(p, m)
    p.meta.update(height=round(max(q[1] for f in p.faces for q in f.pts), 3),
                  footprint=[round(x0, 3), round(-pd - 0.02, 3), round(x1, 3), round(DESK_Z + DESK[1] * math.cos(a), 3)],
                  reader=[0.0, 0.0, 1.0])
    return p


def _finish(p: Piece, m: mk.Mesh) -> Piece:
    p.faces.extend(from_mesh(m, ZONES, walkthrough=p.walkthrough).faces)
    return p


BUILDERS = {CATALOGUE_STAND: catalogue_stand}
CATALOGUE = {CATALOGUE_STAND: {"house": 0, "perch": 1}}
