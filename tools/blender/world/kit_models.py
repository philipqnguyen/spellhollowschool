"""Castle dressing : the station props and the Potions
classroom kit, and the small tells that mark the hidden find spots (pure
Python, builds `meshkit.Mesh`; `kit.py` assembles them per cell and
`greybox.py` bakes them into the castle cells).

Each model is a function returning a `Kit`: a mesh in local coordinates
(metres, Y up, front facing +Z) and a palette. Faces use either an atlas
tile as their material (`wood`, `fabric`, `metal`, `leather`, `leaf`,
`stone`, `glass`, `linen`, `parchment`, `chalk`, `books`, `flame`, `brew`,
`wax`, `dark`; see `kit.TILES`) with a `zone` naming a palette colour that
tints it, or a flat greybox material of `layout_lib.MATERIALS` written
`g:<name>` (the find-spot tells, which then cost no texture at all).

Origins: wall pieces (counter, corners, tables, shelves, boards) stand with
the middle of their back on the wall, at floor level; free-standing pieces
(cauldrons, desks, columns) at the bottom centre; hanging pieces (lamps) at
their ceiling hook. Collision and walkability still come only from the
layout: players walk through dressing, so nothing here may cover a standing
spot that matters (the stations keep theirs clear).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import foliage as FO
import meshkit as mk
from castle_kit import fire as FI
from meshkit import Mesh

# Shared colours (sRGB): materials of the style guide, and some fixed accents.
OAK, AGED_OAK, DARK_OAK = "#7D512E", "#603C24", "#52311B"
IRON, BRASS, PEWTER, SILVER = "#4D4E57", "#CFA027", "#9C9791", "#BCC2C9"
STONE, STONE_DARK = "#9A8D7A", "#71695E"
CREAM, LINEN, PARCHMENT = "#EBE0C6", "#ECE4D4", "#DDCB9E"
WICKER = "#B98B4B"


@dataclass
class Kit:
    mesh: Mesh
    palette: dict[int, str] = field(default_factory=dict)
    # Lights the piece brings (local coordinates; intensities on the scale of
    # the castle lights in props.json), added to the cell manifest by greybox.py.
    lights: list[dict] = field(default_factory=list)
    # Vegetation look A (world/foliage.py): plantings of the foliage kit in the model's frame
    # (the garden gate's hedges), placed with the model by kit.assemble.
    foliage: list = field(default_factory=list)


def _flat(m: Mesh, outline, y: float, mat: str, zone: int, up: bool = True) -> None:
    pts = [(x, y, z) for x, z in outline]
    if (mk.newell(pts)[1] > 0) != up:
        pts.reverse()
    m.poly(pts, mat, zone=zone)


def _disc(m: Mesh, centre, r: float, mat: str, zone: int, n: int = 10, up: bool = True, uv_fit: bool = False) -> None:
    cx, cy, cz = centre
    pts = [(cx + r * math.sin(2 * math.pi * i / n), cy, cz + r * math.cos(2 * math.pi * i / n)) for i in range(n)]
    uvs = [(0.5 + 0.5 * math.sin(2 * math.pi * i / n), 0.5 + 0.5 * math.cos(2 * math.pi * i / n)) for i in range(n)]
    if (mk.newell(pts)[1] > 0) != up:
        pts.reverse()
        uvs.reverse()
    m.poly(pts, mat, uvs if uv_fit else None, zone)


def _ball(m: Mesh, centre, r: float, mat: str, zone: int, seg: int = 7) -> None:
    prof = [(r * math.sin(math.pi * i / 5), -r * math.cos(math.pi * i / 5)) for i in range(6)]
    mk.lathe(m, prof, seg, mat, zone, centre)


def _owl(m: Mesh, base, yaw: float, colour_zone: int, face_zone: int, s: float = 1.0) -> None:
    """A small perched owl: body, head with a pale facial disc, eyes."""
    mark = m.mark()
    mk.lathe(m, [(0.0, 0.0), (0.06, 0.02), (0.075, 0.08), (0.065, 0.14), (0.0, 0.17)], 8, "fabric", colour_zone,
             scale_xz=(1.0, 0.85))
    mk.lathe(m, [(0.0, 0.0), (0.06, 0.02), (0.062, 0.06), (0.0, 0.1)], 8, "fabric", colour_zone, (0.0, 0.15, 0.0))
    # The pale facial disc, facing forward.
    face = [(0.042 * math.sin(2 * math.pi * i / 8), 0.2 + 0.038 * math.cos(2 * math.pi * i / 8), 0.058) for i in range(8)]
    m.poly(face if mk.newell(face)[2] > 0 else list(reversed(face)), "fabric", zone=face_zone)
    for sx in (-1, 1):
        mk.centred_box(m, (sx * 0.018, 0.205, 0.068), (0.014, 0.014, 0.006), "dark", 0)
    mk.lathe(m, [(0.008, 0.0), (0.0, 0.025)], 4, "metal", 90, (0.0, 0.185, 0.07))
    m.transform(mark, lambda p: tuple(b + c for b, c in zip(base, mk.scale(mk.rot_y(p, yaw), s))))


# ---------------------------------------------------------------------------
# The Owl Exchange counter (Entrance Hall)


def owl_exchange_counter(width: float = 8.0) -> Kit:
    """An owl-mail counter: a wall of pigeonholes stuffed with letters and
    parcels, a long panelled counter with brass service grilles, owls on
    T-perches, a brass bell, a ledger, parcel scales and a hanging plaque.
    `width` (castle kit M2: 12 m in the Entrance Hall's Owl Exchange court)
    stretches it along the wall: more cubbies, cupboard doors and panels,
    the grilles, owls and counter-top things spread with it (8 m is the
    M1 model, its shelves, plinth and plaque nudged off coplanar faces)."""
    m = Mesh()
    L, H = float(width), 3.8
    hl = L / 2
    sp = L / 8.0  # spreads the fixed positions along the counter
    doors = round(8 * sp)
    # Pigeonhole wall: carcass, cornice and a grid of cubbies.
    back, front = 0.03, 0.6
    mk.box(m, (-hl, 0.0, back), (hl, 0.6, front), "wood", 2, skip=("-y",))  # base cupboard
    for i in range(doors):
        x0 = -hl + 0.06 + i * (L - 0.12) / doors
        mk.box(m, (x0 + 0.04, 0.08, front), (x0 + (L - 0.12) / doors - 0.04, 0.52, front + 0.012), "wood", 1)
        mk.box(m, (x0 + (L - 0.12) / (2 * doors) - 0.03, 0.29, front + 0.012),
               (x0 + (L - 0.12) / (2 * doors) + 0.03, 0.31, front + 0.03), "metal", 3)
    mk.box(m, (-hl, 0.6, back), (hl, 0.64, front + 0.03), "wood", 2)
    cols, rows = round(16 * sp), 8
    cw, rh = (L - 0.16) / cols, (H - 0.95) / rows
    y0 = 0.66
    mk.box(m, (-hl, y0, back - 0.02), (hl, y0 + rows * rh, back), "dark", 0)  # backboard
    for c in range(cols + 1):
        x = -hl + 0.08 + c * cw
        mk.box(m, (x - 0.015, y0, back), (x + 0.015, y0 + rows * rh, front - 0.04), "wood", 2, skip=("-z",))
    for r in range(rows + 1):
        y = y0 + r * rh
        # (The shelves stop inside the end dividers and 5 mm behind the
        # dividers' fronts: no coplanar faces where they cross, castle kit M2.)
        mk.box(m, (-hl + 0.095, y - 0.012, back), (hl - 0.095, y + 0.012, front - 0.045), "wood", 2, skip=("-z",))
    # Letters and parcels in some cubbies (deterministic scatter).
    seed = 7
    for c in range(cols):
        for r in range(rows):
            seed = (seed * 1103515245 + 12345) % 2**31
            k = seed % 7
            x0 = -hl + 0.08 + c * cw
            yb = y0 + r * rh + 0.012
            if k in (0, 1, 2):  # a bundle of letters lying in the cubby
                mk.box(m, (x0 + 0.03, yb, back + 0.06), (x0 + cw - 0.03, yb + 0.05 + 0.02 * k, front - 0.1), "parchment",
                       0, skip=("-y",))
            elif k == 3:  # a parcel tied with string
                mk.box(m, (x0 + 0.05, yb, back + 0.08), (x0 + cw - 0.06, yb + rh * 0.55, front - 0.08), "leather", 4,
                       skip=("-y",))
            elif k == 4:  # a rolled scroll
                mk.rod(m, (x0 + 0.04, yb + 0.03, front - 0.14), (x0 + cw - 0.04, yb + 0.03, front - 0.14), 0.028, 6,
                       "parchment", 0)
    top = y0 + rows * rh
    mk.box(m, (-hl - 0.04, top, back), (hl + 0.04, top + 0.12, front + 0.05), "wood", 2)
    mk.box(m, (-hl - 0.07, top + 0.12, back), (hl + 0.07, top + 0.18, front + 0.09), "wood", 3)
    # The counter.
    c0, c1, ch = 1.55, 2.55, 1.08
    mk.box(m, (-hl + 0.2, 0.0, c0 + 0.06), (hl - 0.2, ch - 0.06, c1 - 0.06), "wood", 1, skip=("-y", "+y"))
    mk.box(m, (-hl + 0.12, ch - 0.06, c0), (hl - 0.12, ch, c1 + 0.04), "wood", 2)
    mk.box(m, (-hl + 0.21, 0.0, c1 - 0.08), (hl - 0.21, 0.1, c1 - 0.04), "wood", 2, skip=("-y",))  # plinth
    panels = round(8 * sp)
    for i in range(panels):
        x0 = -hl + 0.3 + i * (L - 0.6) / panels
        x1 = x0 + (L - 0.6) / panels - 0.1
        mk.box(m, (x0, 0.2, c1 - 0.06), (x1, ch - 0.18, c1 - 0.045), "wood", 2)
        mk.box(m, (x0 + 0.06, 0.26, c1 - 0.045), (x1 - 0.06, ch - 0.24, c1 - 0.035), "wood", 1)
    mk.rod(m, (-hl + 0.2, 0.92, c1 + 0.08), (hl - 0.2, 0.92, c1 + 0.08), 0.018, 6, "metal", 3)
    for x in (-hl + 0.3, -1.3 * sp, 1.3 * sp, hl - 0.3):
        mk.rod(m, (x, 0.92, c1 - 0.05), (x, 0.92, c1 + 0.08), 0.012, 5, "metal", 3)
    # Brass grilles: three service windows with arched frames and bars.
    for gx in (-2.6 * sp, 0.0, 2.6 * sp):
        arch = mk.arch_outline(1.1, 0.42, "round", 0.0, 6)
        mark = m.mark()
        ring_pts = [(x, y) for x, y in arch]
        inner = [(x * 0.86, 0.04 + y * 0.9) for x, y in arch]
        for i in range(len(ring_pts)):
            j = (i + 1) % len(ring_pts)
            quad = [(ring_pts[i][0], ring_pts[i][1], 0.0), (ring_pts[j][0], ring_pts[j][1], 0.0),
                    (inner[j][0], inner[j][1], 0.0), (inner[i][0], inner[i][1], 0.0)]
            if mk.newell(quad)[2] < 0:
                quad.reverse()
            m.poly(quad, "metal", zone=3)
            m.poly(list(reversed(quad)), "metal", zone=3)
        for k in range(1, 8):
            x = -0.47 + 0.94 * k / 8
            ytop = 0.42 + math.sqrt(max(0.0, 0.47 ** 2 - x * x)) * 0.9
            mk.rod(m, (x, 0.04, 0.0), (x, ytop, 0.0), 0.006, 4, "metal", 3, caps=False)
        m.place(mark, 0.0, (gx, ch, c0 + 0.12))
        mk.box(m, (gx - 0.24, ch, c0 + 0.2), (gx + 0.24, ch + 0.012, c0 + 0.62), "leather", 5)  # blotter
    # A brass bell, a ledger, parcel scales and an inkpot with a quill.
    mk.lathe(m, [(0.05, 0.0), (0.05, 0.01), (0.038, 0.03), (0.03, 0.06), (0.008, 0.075), (0.0, 0.085)], 8, "metal", 3,
             (-1.3 * sp, ch, c1 - 0.2))
    sc, ink = 3.4 * sp, 0.6 * sp
    mk.box(m, (1.0 * sp, ch, c1 - 0.42), (1.36 * sp, ch + 0.05, c1 - 0.16), "leather", 6)
    mk.box(m, (1.02 * sp, ch + 0.05, c1 - 0.4), (1.34 * sp, ch + 0.06, c1 - 0.18), "linen", 0, skip=("-y",))
    mk.lathe(m, [(0.08, 0.0), (0.07, 0.02), (0.015, 0.03), (0.015, 0.3)], 8, "metal", 3, (sc, ch, c1 - 0.3))
    mk.rod(m, (3.22 * sp, ch + 0.3, c1 - 0.3), (3.58 * sp, ch + 0.3, c1 - 0.3), 0.01, 4, "metal", 3)
    for sx in (-1, 1):
        mk.lathe(m, [(0.0, 0.0), (0.07, 0.01), (0.075, 0.03)], 8, "metal", 3, (sc + sx * 0.18, ch + 0.2, c1 - 0.3),
                 cap_top=False)
    mk.lathe(m, [(0.025, 0.0), (0.03, 0.03), (0.012, 0.045)], 6, "glass", 7, (ink, ch, c1 - 0.25))
    mk.rod(m, (ink, ch + 0.04, c1 - 0.25), (0.66 * sp, ch + 0.2, c1 - 0.3), 0.006, 3, "linen", 0)
    # Owls on T-perches between the counter and the pigeonholes.
    for i, (px, owl_zone) in enumerate(((-3.4 * sp, 8), (-1.0 * sp, 9), (1.6 * sp, 10), (3.0 * sp, 8))):
        pz = 1.05
        mk.lathe(m, [(0.09, 0.0), (0.08, 0.03), (0.02, 0.05), (0.02, 1.35)], 6, "wood", 2, (px, 0.0, pz))
        mk.rod(m, (px - 0.28, 1.36, pz), (px + 0.28, 1.36, pz), 0.02, 5, "wood", 2)
        _owl(m, (px - 0.1 + 0.15 * (i % 2), 1.37, pz), 0.3 if i % 2 else -0.4, owl_zone, 11, s=1.5)
    # A plaque with a gilt owl silhouette above the pigeonholes.
    mk.box(m, (-0.9, top + 0.24, front - 0.02), (0.9, top + 0.74, front + 0.04), "wood", 1)
    wing = [(-0.55, 0.0), (-0.2, 0.12), (0.0, 0.32), (0.2, 0.12), (0.55, 0.0), (0.2, 0.05), (0.0, 0.1), (-0.2, 0.05)]
    mark = m.mark()
    mk.prism(m, wing, 0.045, 0.055, "metal", 3)
    m.place(mark, 0.0, (0.0, top + 0.33, front))
    for x in (-0.6, 0.6):
        mk.rod(m, (x, top + 0.74, front + 0.01), (x, top + 1.2, front - 0.3), 0.008, 4, "metal", 12, caps=False)
    pal = {1: OAK, 2: AGED_OAK, 3: BRASS, 4: "#986F47", 5: "#386038", 6: "#6E1422", 7: "#324369",
           8: "#986F47", 9: "#EBE3DA", 10: "#B98B4B", 11: "#F1E6D4", 12: IRON, 90: "#CFA027"}
    lights = [{"pos": [-2.6 * sp, 1.6, 1.9], "color": "#FFC46E", "intensity": 6, "range": 7.0},
              {"pos": [2.6 * sp, 1.6, 1.9], "color": "#FFC46E", "intensity": 6, "range": 7.0}]
    return Kit(m, pal, lights)


# ---------------------------------------------------------------------------
# Common-room crafting stations


def knitting_corner() -> Kit:
    """The Charms crafting corner: enchanted needles knitting in mid-air over
    a wicker basket of yarn, a tailor's dummy with a tape measure, a sewing
    table with spools and a pincushion, and a shelf of cloth bolts."""
    m = Mesh()
    # Wall shelf with bolts of cloth.
    mk.box(m, (-0.95, 1.5, 0.02), (0.95, 1.54, 0.34), "wood", 1)
    for sx in (-1, 1):
        mk.box(m, (sx * 0.8 - 0.02, 1.3, 0.02), (sx * 0.8 + 0.02, 1.5, 0.3), "wood", 1)
    for i in range(7):
        x = -0.8 + i * 0.26
        mk.rod(m, (x, 1.62, 0.06), (x, 1.62, 0.3), 0.075, 8, "fabric", 20 + i % 5)
    # Wicker basket with yarn balls.
    bx, bz = -0.45, 0.62
    mk.lathe(m, [(0.16, 0.0), (0.22, 0.05), (0.25, 0.22), (0.26, 0.26), (0.24, 0.26)], 10, "leather", 2, (bx, 0.0, bz),
             cap_top=False, cap_bottom=True)
    _disc(m, (bx, 0.2, bz), 0.24, "dark", 0)
    for i, (dx, dz, r) in enumerate(((-0.08, 0.02, 0.09), (0.08, -0.04, 0.085), (0.0, 0.1, 0.08), (0.1, 0.09, 0.07))):
        _ball(m, (bx + dx, 0.2 + r * 0.7, bz + dz), r, "fabric", 20 + (i + 2) % 5)
    # The enchanted knitting floating over the basket.
    ky = 1.05
    for sx in (-1, 1):
        mk.rod(m, (bx - 0.18 * sx, ky - 0.1, bz + 0.05), (bx + 0.2 * sx, ky + 0.14, bz - 0.02), 0.006, 4, "metal", 3)
        _ball(m, (bx - 0.18 * sx, ky - 0.1, bz + 0.05), 0.012, "metal", 3, 5)
    rows = 5
    for r in range(rows):
        y0, y1 = ky - 0.02 - r * 0.045, ky - 0.065 - r * 0.045
        sw = 0.15 + 0.01 * r
        quad = [(bx - sw, y1, bz + 0.03), (bx + sw, y1, bz + 0.03), (bx + sw, y0, bz + 0.03), (bx - sw, y0, bz + 0.03)]
        m.poly(quad, "fabric", zone=21 if r % 2 else 22)
        m.poly(list(reversed(quad)), "fabric", zone=21 if r % 2 else 22)
    for t in range(6):
        a = (bx + 0.12, ky - 0.24 - t * 0.12, bz + 0.03 + 0.01 * t)
        b = (bx + 0.09 - 0.02 * t, ky - 0.36 - t * 0.12, bz + 0.04)
        mk.rod(m, a, b, 0.004, 3, "fabric", 22, caps=False)
    # The tailor's dummy on a turned stand.
    dx, dz = 0.45, 0.5
    prof = [(0.0, 1.05), (0.12, 1.06), (0.15, 1.12), (0.13, 1.25), (0.155, 1.36), (0.17, 1.44), (0.12, 1.52),
            (0.05, 1.56), (0.04, 1.6), (0.0, 1.62)]
    mk.lathe(m, prof, 10, "fabric", 6, (dx, 0.0, dz), scale_xz=(1.0, 0.72))
    mk.lathe(m, [(0.02, 0.1), (0.02, 1.05)], 6, "wood", 1, (dx, 0.0, dz), cap_top=False)
    mk.lathe(m, [(0.03, 1.6), (0.035, 1.66), (0.0, 1.68)], 6, "wood", 1, (dx, 0.0, dz))
    for k in range(3):
        a = 2 * math.pi * k / 3 + 0.4
        mk.rod(m, (dx, 0.12, dz), (dx + 0.24 * math.sin(a), 0.018, dz + 0.24 * math.cos(a)), 0.015, 4, "wood", 1)
    # A tape measure draped round the dummy's neck.
    for i in range(10):
        t0, t1 = i / 10, (i + 1) / 10
        def tp(t, side):
            a = math.pi * (0.15 + 0.7 * t)
            return (dx + side * 0.0 + 0.14 * math.cos(a) * 1.05, 1.52 - 0.35 * abs(math.cos(a)) ** 3,
                    dz + 0.11 + 0.02 * math.sin(a))
        p0, p1 = tp(t0, 1), tp(t1, 1)
        quad = [p0, p1, mk.add(p1, (0, -0.02, 0)), mk.add(p0, (0, -0.02, 0))]
        m.poly(quad, "linen", zone=0)
        m.poly(list(reversed(quad)), "linen", zone=0)
    # Sewing table with spools, scissors and a pincushion.
    tx, tz = 0.0, 0.35
    mk.lathe(m, [(0.25, 0.68), (0.27, 0.7), (0.27, 0.72), (0.25, 0.73)], 12, "wood", 1, (tx, 0.0, tz), cap_top=True,
             cap_bottom=True)
    mk.lathe(m, [(0.05, 0.0), (0.03, 0.08), (0.03, 0.62), (0.05, 0.68)], 6, "wood", 1, (tx, 0.0, tz), cap_top=False)
    for k in range(3):
        a = 2 * math.pi * k / 3
        mk.rod(m, (tx, 0.08, tz), (tx + 0.2 * math.sin(a), 0.018, tz + 0.2 * math.cos(a)), 0.014, 4, "wood", 1)
    for i, (sx, sz) in enumerate(((-0.12, 0.05), (-0.04, -0.1), (0.07, 0.08))):
        mk.lathe(m, [(0.022, 0.0), (0.022, 0.006), (0.016, 0.008), (0.016, 0.042), (0.022, 0.044), (0.022, 0.05)], 6,
                 "fabric", 20 + i, (tx + sx, 0.73, tz + sz))
    _ball(m, (tx + 0.14, 0.76, tz - 0.06), 0.04, "fabric", 6)
    mk.box(m, (tx - 0.02, 0.73, tz + 0.12), (tx + 0.1, 0.735, tz + 0.14), "metal", 4)
    # A rag rug under it all, and a low footstool by the basket.
    rug = [(1.0 * math.sin(2 * math.pi * i / 16), 0.7 * math.cos(2 * math.pi * i / 16) + 0.78) for i in range(16)]
    _flat(m, [(x, z) for x, z in rug], 0.006, "fabric", 25)
    _flat(m, [(x * 0.8, (z - 0.78) * 0.8 + 0.78) for x, z in rug], 0.008, "fabric", 20)
    mk.lathe(m, [(0.16, 0.0), (0.18, 0.05), (0.18, 0.22), (0.15, 0.26)], 10, "fabric", 23, (-0.85, 0.0, 0.85),
             cap_top=True)
    pal = {1: OAK, 2: WICKER, 3: SILVER, 4: SILVER, 6: CREAM, 20: "#9C1F1F", 21: "#CFA027", 22: "#EBE0C6",
           23: "#577D3B", 24: "#28528A", 25: "#7D513B"}
    return Kit(m, pal)


def transfiguration_table() -> Kit:
    """The Transfiguration table: a heavy oak table with a gilt practice
    circle inlaid in its top, objects half-way through a lesson (a teacup,
    a goblet, a feather, a matchbox), an open book, a candle and a stool."""
    m = Mesh()
    w, d, h = 1.6, 0.8, 0.8
    z0, z1 = 0.08, 0.08 + d
    zc = (z0 + z1) / 2
    mk.box(m, (-w / 2, h - 0.06, z0), (w / 2, h, z1), "wood", 1)
    mk.box(m, (-w / 2 + 0.06, h - 0.16, z0 + 0.06), (w / 2 - 0.06, h - 0.06, z1 - 0.06), "wood", 2, skip=("+y",))
    for sx in (-1, 1):
        for zz in (z0 + 0.08, z1 - 0.08):
            prof = [(0.05, 0.0), (0.06, 0.04), (0.04, 0.2), (0.055, 0.38), (0.035, 0.55), (0.05, h - 0.16)]
            mk.lathe(m, prof, 6, "wood", 1, (sx * (w / 2 - 0.09), 0.0, zz), cap_top=False)
    mk.box(m, (-w / 2 + 0.1, 0.12, zc - 0.03), (w / 2 - 0.1, 0.16, zc + 0.03), "wood", 2)
    # The practice circle: a gilt ring and a star inlay.
    ring_n = 20
    for i in range(ring_n):
        a0, a1 = 2 * math.pi * i / ring_n, 2 * math.pi * (i + 1) / ring_n
        for r0, r1 in ((0.26, 0.29),):
            quad = [(r1 * math.sin(a0), h + 0.001, zc + r1 * math.cos(a0)), (r1 * math.sin(a1), h + 0.001, zc + r1 * math.cos(a1)),
                    (r0 * math.sin(a1), h + 0.001, zc + r0 * math.cos(a1)), (r0 * math.sin(a0), h + 0.001, zc + r0 * math.cos(a0))]
            if mk.newell(quad)[1] < 0:
                quad.reverse()
            m.poly(quad, "metal", zone=3)
    star = []
    for i in range(10):
        r = 0.2 if i % 2 == 0 else 0.08
        a = math.pi * i / 5
        star.append((r * math.sin(a), h + 0.0015, zc + r * math.cos(a)))
    m.poly(star if mk.newell(star)[1] > 0 else list(reversed(star)), "metal", zone=3)
    # A teacup on its saucer in the circle, a goblet and a feather beside it.
    mk.lathe(m, [(0.0, 0.0), (0.07, 0.004), (0.075, 0.012)], 10, "glass", 4, (0.0, h + 0.002, zc), cap_top=False)
    mk.lathe(m, [(0.025, 0.012), (0.04, 0.02), (0.05, 0.06), (0.052, 0.07), (0.045, 0.07), (0.035, 0.03)], 10, "glass", 4,
             (0.0, h + 0.002, zc), cap_top=False, cap_bottom=True)
    mk.lathe(m, [(0.035, 0.0), (0.012, 0.01), (0.008, 0.08), (0.035, 0.1), (0.045, 0.15), (0.04, 0.17)], 8, "metal", 5,
             (0.45, h, zc - 0.15), cap_top=False, cap_bottom=True)
    feather = [(0.0, 0.0), (0.02, 0.03), (0.025, 0.14), (0.008, 0.2), (-0.012, 0.12), (-0.015, 0.03)]
    _flat(m, [(-0.42 + x, zc + 0.12 - y) for x, y in feather], h + 0.003, "linen", 0)
    mk.box(m, (0.32, h, zc + 0.12), (0.42, h + 0.025, zc + 0.18), "parchment", 0)
    # An open book and a candle.
    mk.box(m, (-0.72, h, z0 + 0.12), (-0.38, h + 0.02, z0 + 0.36), "leather", 6)
    for sx in (-1, 1):
        lo, hi = (-0.55, -0.385) if sx > 0 else (-0.715, -0.55)
        quad = [(lo, h + 0.022, z0 + 0.36), (hi, h + 0.022 + 0.012 * (sx > 0), z0 + 0.36), (hi, h + 0.022, z0 + 0.13),
                (lo, h + 0.022, z0 + 0.13)]
        m.poly(quad if mk.newell(quad)[1] > 0 else list(reversed(quad)), "parchment", zone=0)
    mk.lathe(m, [(0.05, 0.0), (0.05, 0.01), (0.015, 0.02), (0.015, 0.05), (0.03, 0.06)], 8, "metal", 5,
             (0.62, h, z0 + 0.15), cap_top=False, cap_bottom=True)
    mk.cylinder(m, (0.62, h + 0.06, z0 + 0.15), 0.014, 0.12, 6, "wax", 0)
    FI.note(m, candles=[(0.62, h + 0.185, z0 + 0.15, 0.055)])  # the flame: the client's animated fire
    # A stool tucked in at the front.
    mk.lathe(m, [(0.17, 0.44), (0.18, 0.46), (0.18, 0.48), (0.17, 0.49)], 10, "wood", 1, (0.25, 0.0, z1 + 0.35),
             cap_top=True, cap_bottom=True)
    for k in range(3):
        a = 2 * math.pi * k / 3
        mk.rod(m, (0.25 + 0.15 * math.sin(a), 0.012, z1 + 0.35 + 0.15 * math.cos(a)),
               (0.25 + 0.08 * math.sin(a), 0.44, z1 + 0.35 + 0.08 * math.cos(a)), 0.018, 4, "wood", 1)
    # A stack of books at the back, and a chalk diagram of the lesson on the wall.
    for i, (dy, zone) in enumerate(((0.07, 7), (0.05, 8), (0.06, 6))):
        y = h + (0.0, 0.07, 0.12)[i]
        mk.box(m, (-0.2 + 0.01 * i, y, z0 + 0.04), (0.06 - 0.01 * i, y + dy, z0 + 0.26), "leather", zone)
    m.poly([(-0.6, 1.25, 0.02), (0.6, 1.25, 0.02), (0.6, 1.95, 0.02), (-0.6, 1.95, 0.02)], "chalk",
           [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], 0)
    for (a, b) in (((-0.64, 1.21), (0.64, 1.25)), ((-0.64, 1.95), (0.64, 1.99)), ((-0.64, 1.25), (-0.6, 1.95)),
                   ((0.6, 1.25), (0.64, 1.95))):
        mk.box(m, (a[0], a[1], 0.0), (b[0], b[1], 0.04), "wood", 2)
    pal = {1: OAK, 2: AGED_OAK, 3: BRASS, 4: "#E4EFF4", 5: SILVER, 6: "#432C69", 7: "#6E1422", 8: "#386038"}
    lights = [{"pos": [0.62, 1.05, 0.23], "color": "#FFC46E", "intensity": 2.5, "range": 3.5}]
    return Kit(m, pal, lights)


# ---------------------------------------------------------------------------
# The Potions classroom


def classroom_cauldron() -> Kit:
    """A student's brewing place: a black iron cauldron on a tripod over a
    fire, with a small bench of ingredient jars, a mortar and a chopping
    board with roots."""
    import furniture_models as fm

    m = Mesh()
    # Fire bowl and embers.
    mk.lathe(m, [(0.16, 0.0), (0.22, 0.1), (0.23, 0.12)], 10, "metal", 2, cap_top=False, cap_bottom=True)
    ember = [(0.2 * math.sin(2 * math.pi * i / 10), 0.11, 0.2 * math.cos(2 * math.pi * i / 10)) for i in range(10)]
    uvs = [(0.5 + 0.4 * math.sin(2 * math.pi * i / 10), 0.35 + 0.3 * math.cos(2 * math.pi * i / 10)) for i in range(10)]
    m.poly(ember if mk.newell(ember)[1] > 0 else list(reversed(ember)), "flame",
           uvs if mk.newell(ember)[1] > 0 else list(reversed(uvs)), 0)
    # The flames licking up round the cauldron: the client's animated fire (docs/architecture.md).
    FI.note(m, fires=[FI.fire((0.0, 0.1, 0.0), FI.cluster_tongues(0.11, 0.16, count=4, seed=19), embers=2)])
    for k in range(3):
        a = 2 * math.pi * k / 3 + 0.4
        mk.rod(m, (0.42 * math.sin(a), 0.0, 0.42 * math.cos(a)), (0.3 * math.sin(a), 0.62, 0.3 * math.cos(a)), 0.02, 5,
               "metal", 2)
    fm.cauldron(0.36, 0.3, 0.86, m, zone=1)
    # Side bench with jars, a mortar and pestle and a chopping board.
    bx, bz = 0.85, 0.0
    mk.box(m, (bx - 0.25, 0.68, bz - 0.4), (bx + 0.25, 0.72, bz + 0.4), "wood", 3)
    for sx in (-1, 1):
        for sz in (-1, 1):
            mk.box(m, (bx + sx * 0.2 - 0.025, 0.0, bz + sz * 0.34 - 0.025), (bx + sx * 0.2 + 0.025, 0.68,
                   bz + sz * 0.34 + 0.025), "wood", 3, skip=("-y",))
    for i, (jx, jz, jh) in enumerate(((-0.12, -0.28, 0.14), (0.0, -0.3, 0.1), (0.12, -0.26, 0.17), (-0.1, 0.3, 0.12))):
        mk.lathe(m, [(0.035, 0.0), (0.04, 0.01), (0.04, jh), (0.025, jh + 0.015), (0.025, jh + 0.03)], 6, "glass",
                 10 + i, (bx + jx, 0.72, bz + jz))
        mk.cylinder(m, (bx + jx, 0.72 + jh + 0.03, bz + jz), 0.027, 0.015, 6, "wood", 3)
    mk.lathe(m, [(0.04, 0.0), (0.06, 0.03), (0.065, 0.07), (0.05, 0.07)], 8, "stone", 4, (bx + 0.08, 0.72, bz + 0.12),
             cap_top=False, cap_bottom=True)
    mk.rod(m, (bx + 0.08, 0.76, bz + 0.12), (bx + 0.13, 0.86, bz + 0.15), 0.012, 4, "stone", 4)
    mk.box(m, (bx - 0.18, 0.72, bz - 0.12), (bx + 0.0, 0.735, bz + 0.12), "wood", 5)
    for i in range(3):
        mk.rod(m, (bx - 0.15 + 0.05 * i, 0.745, bz - 0.06), (bx - 0.13 + 0.05 * i, 0.745, bz + 0.07), 0.01, 4, "leather",
               6)
    pal = {1: "#42444B", 2: IRON, 3: DARK_OAK, 4: STONE, 5: OAK, 6: "#986F33", 10: "#5F9C2A", 11: "#8A225A",
           12: "#CF7419", 13: "#1F57B2"}
    lights = [{"pos": [0.0, 0.95, 0.0], "color": "#A4E276", "intensity": 4, "range": 5.0},
              {"pos": [0.0, 0.2, 0.0], "color": "#FF9440", "intensity": 5, "range": 4.0}]
    return Kit(m, pal, lights)


def potions_shelves() -> Kit:
    """Tall dark shelves along a dungeon wall, crowded with jars and bottles
    of coloured ingredients, pickled specimens and labelled boxes."""
    m = Mesh()
    w, h, d = 4.0, 3.2, 0.45
    hw = w / 2
    mk.box(m, (-hw, 0.0, 0.02), (hw, h, 0.05), "dark", 0)
    for sx in (-1, 0, 1):
        x = sx * (hw - 0.03) if sx else 0.0
        mk.box(m, (x - 0.035, 0.0, 0.05), (x + 0.035, h, d), "wood", 1, skip=("-y",))
    shelves = [0.1, 0.72, 1.34, 1.96, 2.58, 3.15]
    for y in shelves:
        mk.box(m, (-hw, y, 0.05), (hw, y + 0.04, d + 0.02), "wood", 1)
    seed = 11
    zones = [10, 11, 12, 13, 14, 15, 16, 17]
    for si, y in enumerate(shelves[:-1]):
        for half in (-1, 1):
            x = (-hw + 0.08) if half < 0 else 0.08
            limit = -0.08 if half < 0 else hw - 0.08
            while True:
                seed = (seed * 1103515245 + 12345) % 2**31
                kind = seed % 5
                r = 0.04 + (seed >> 5) % 30 / 1000.0
                hh = 0.12 + (seed >> 9) % 25 / 100.0
                hh = min(hh, shelves[si + 1] - y - 0.12)
                if x + 2 * r > limit:
                    break
                cx, base = x + r, y + 0.04
                zone = zones[(seed >> 13) % len(zones)]
                if kind == 4:  # a labelled box
                    mk.box(m, (cx - r, base, 0.12), (cx + r, base + hh * 0.6, 0.38), "leather", 5)
                    mk.box(m, (cx - r * 0.7, base + hh * 0.2, 0.38), (cx + r * 0.7, base + hh * 0.4, 0.385), "parchment", 0)
                else:
                    neck = 0.5 if kind < 2 else 0.85
                    mk.lathe(m, [(r, 0.0), (r, hh * 0.8), (r * neck, hh)], 6, "glass", zone, (cx, base, 0.25), cap_top=False)
                    mk.cylinder(m, (cx, base + hh, 0.25), r * neck * 1.1, 0.02, 5, "wood", 2)
                    if kind == 3:  # a specimen inside
                        mk.lathe(m, [(0.0, 0.0), (r * 0.5, hh * 0.15), (r * 0.4, hh * 0.5), (0.0, hh * 0.65)], 5, "dark",
                                 0, (cx, base + 0.02, 0.25 + r * 0.98))
                    m.poly([(cx - r * 0.55, base + hh * 0.35, 0.25 + r * 1.01), (cx + r * 0.55, base + hh * 0.35, 0.25 + r * 1.01),
                            (cx + r * 0.55, base + hh * 0.55, 0.25 + r * 1.01), (cx - r * 0.55, base + hh * 0.55, 0.25 + r * 1.01)],
                           "parchment", zone=0)
                x += 2 * r + 0.015
    mk.box(m, (-hw - 0.05, h, 0.02), (hw + 0.05, h + 0.1, d + 0.06), "wood", 1)
    pal = {1: DARK_OAK, 2: AGED_OAK, 5: "#6E4128", 10: "#5F9C2A", 11: "#8A225A", 12: "#CF7419", 13: "#1F57B2",
           14: "#A8206F", 15: "#BDCB60", 16: "#4C749C", 17: "#98331E"}
    return Kit(m, pal)


def potions_blackboard() -> Kit:
    """A slate blackboard on the wall with chalk diagrams, in a dark frame
    with a chalk ledge."""
    m = Mesh()
    w, y0, y1 = 3.0, 1.0, 2.5
    m.poly([(-w / 2, y0, 0.06), (w / 2, y0, 0.06), (w / 2, y1, 0.06), (-w / 2, y1, 0.06)], "chalk",
           [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)], 0)
    for (a, b) in (((-w / 2 - 0.06, y0 - 0.06), (w / 2 + 0.06, y0)), ((-w / 2 - 0.06, y1), (w / 2 + 0.06, y1 + 0.06)),
                   ((-w / 2 - 0.06, y0), (-w / 2, y1)), ((w / 2, y0), (w / 2 + 0.06, y1))):
        mk.box(m, (a[0], a[1], 0.02), (b[0], b[1], 0.08), "wood", 1)
    mk.box(m, (-w / 2, y0 - 0.1, 0.02), (w / 2, y0 - 0.06, 0.16), "wood", 1)
    for x in (-1.0, -0.6, 0.9):
        mk.box(m, (x, y0 - 0.06, 0.1), (x + 0.07, y0 - 0.045, 0.115), "linen", 0)
    return Kit(m, {1: DARK_OAK})


def teacher_desk() -> Kit:
    """The Potions master's desk: a heavy dark desk with drawer pedestals,
    stacked books, a candle, a rack of vials and brass scales."""
    m = Mesh()
    w, d, h = 2.0, 0.9, 0.82
    mk.box(m, (-w / 2, h - 0.06, -d / 2), (w / 2, h, d / 2), "wood", 1)
    for sx in (-1, 1):
        x0, x1 = sorted((sx * (w / 2 - 0.04), sx * (w / 2 - 0.5)))
        mk.box(m, (x0, 0.0, -d / 2 + 0.04), (x1, h - 0.06, d / 2 - 0.04), "wood", 1, skip=("-y", "+y"))
        for i in range(3):
            y = 0.06 + i * 0.24
            mk.box(m, (x0 + 0.03, y, -d / 2 + 0.02), (x1 - 0.03, y + 0.2, -d / 2 + 0.04), "wood", 2)
            mk.box(m, ((x0 + x1) / 2 - 0.04, y + 0.09, -d / 2), ((x0 + x1) / 2 + 0.04, y + 0.11, -d / 2 + 0.02), "metal", 3)
    mk.box(m, (-w / 2 + 0.5, 0.3, -d / 2 + 0.04), (w / 2 - 0.5, h - 0.06, -d / 2 + 0.07), "wood", 2)
    for i, (dy, zone) in enumerate(((0.06, 5), (0.05, 6), (0.07, 7))):
        y = h + sum(v for v, _ in ((0.06, 5), (0.05, 6), (0.07, 7))[:i])
        mk.box(m, (-0.85 + 0.02 * i, y, -0.2), (-0.55 - 0.01 * i, y + dy, 0.12), "leather", zone)
    mk.lathe(m, [(0.06, 0.0), (0.06, 0.01), (0.015, 0.02), (0.015, 0.06), (0.035, 0.07)], 8, "metal", 3, (0.75, h, -0.25),
             cap_top=False, cap_bottom=True)
    mk.cylinder(m, (0.75, h + 0.07, -0.25), 0.016, 0.16, 6, "wax", 0)
    FI.note(m, candles=[(0.75, h + 0.23, -0.25, 0.06)])  # the flame: the client's animated fire
    mk.box(m, (0.1, h, 0.05), (0.5, h + 0.03, 0.15), "wood", 2)
    for i in range(5):
        x = 0.14 + i * 0.08
        mk.lathe(m, [(0.015, 0.0), (0.016, 0.1), (0.008, 0.12), (0.008, 0.14)], 5, "glass", 10 + i, (x, h + 0.03, 0.1))
    mk.lathe(m, [(0.06, 0.0), (0.05, 0.02), (0.012, 0.03), (0.012, 0.26)], 8, "metal", 3, (-0.2, h, -0.15))
    mk.rod(m, (-0.36, h + 0.26, -0.15), (-0.04, h + 0.26, -0.15), 0.008, 4, "metal", 3)
    for sx in (-1, 1):
        mk.lathe(m, [(0.0, 0.0), (0.06, 0.01), (0.065, 0.025)], 8, "metal", 3, (-0.2 + sx * 0.16, h + 0.17, -0.15),
                 cap_top=False)
    pal = {1: "#472E20", 2: DARK_OAK, 3: BRASS, 5: "#1B306E", 6: "#386038", 7: "#6E1422", 10: "#5F9C2A", 11: "#8A225A",
           12: "#CF7419", 13: "#1F57B2", 14: "#BDCB60"}
    lights = [{"pos": [0.75, 1.15, -0.25], "color": "#FFC46E", "intensity": 3, "range": 4.5}]
    return Kit(m, pal, lights)


def apothecary_cupboard() -> Kit:
    """An apothecary chest of many small drawers with brass knobs and
    parchment labels, under a cornice."""
    m = Mesh()
    w, h, d = 1.6, 2.3, 0.55
    mk.box(m, (-w / 2, 0.0, 0.02), (w / 2, h, d), "wood", 1, skip=("-y",))
    cols, rows = 6, 9
    for c in range(cols):
        for r in range(rows):
            x0 = -w / 2 + 0.05 + c * (w - 0.1) / cols
            x1 = x0 + (w - 0.1) / cols - 0.02
            y0 = 0.1 + r * (h - 0.2) / rows
            y1 = y0 + (h - 0.2) / rows - 0.02
            mk.box(m, (x0, y0, d), (x1, y1, d + 0.015), "wood", 2)
            xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
            mk.box(m, (xm - 0.05, ym + 0.02, d + 0.015), (xm + 0.05, ym + 0.05, d + 0.018), "parchment", 0)
            mk.box(m, (xm - 0.01, ym - 0.03, d + 0.015), (xm + 0.01, ym - 0.01, d + 0.035), "metal", 3)
    mk.box(m, (-w / 2 - 0.05, h, 0.02), (w / 2 + 0.05, h + 0.1, d + 0.05), "wood", 1)
    return Kit(m, {1: DARK_OAK, 2: AGED_OAK, 3: BRASS})


def barrels() -> Kit:
    """Three ingredient barrels with iron hoops, one with its lid off and
    full of roots."""
    m = Mesh()
    for i, (x, z, s) in enumerate(((-0.35, 0.0, 1.0), (0.35, 0.05, 0.92), (0.0, -0.45, 0.85))):
        h = 0.9 * s
        r = 0.3 * s
        prof = [(r * 0.85, 0.0), (r, h * 0.3), (r * 1.04, h * 0.5), (r, h * 0.7), (r * 0.85, h)]
        mk.lathe(m, prof, 12, "wood", 1, (x, 0.0, z), cap_top=i != 2, cap_bottom=False)
        for y in (0.08 * h, 0.38 * h, 0.62 * h, 0.92 * h):
            rr = r * (0.9 + 0.14 * math.sin(math.pi * y / h)) + 0.004
            mk.lathe(m, [(rr, y - 0.02), (rr, y + 0.02)], 12, "metal", 2, (x, 0.0, z), cap_top=False)
        if i == 2:
            _disc(m, (x, h - 0.06, z), r * 0.84, "dark", 0)
            for k in range(6):
                a = k * 1.1
                mk.rod(m, (x + 0.1 * math.sin(a), h - 0.06, z + 0.1 * math.cos(a)),
                       (x + 0.16 * math.sin(a + 0.6), h + 0.08, z + 0.16 * math.cos(a + 0.6)), 0.02, 4, "leather", 3)
    return Kit(m, {1: AGED_OAK, 2: IRON, 3: "#986F33"})


def stone_column(height: float = 5.4) -> Kit:
    """A squat dungeon column: a square plinth, an octagonal shaft and a
    corbel capital meeting the vault."""
    m = Mesh()
    mk.box(m, (-0.4, 0.0, -0.4), (0.4, 0.35, 0.4), "stone", 1, skip=("-y",))
    mk.box(m, (-0.36, 0.35, -0.36), (0.36, 0.45, 0.36), "stone", 2)
    mk.lathe(m, [(0.3, 0.45), (0.3, height - 0.7)], 8, "stone", 1, phase=math.pi / 8, cap_top=False)
    mk.lathe(m, [(0.3, height - 0.7), (0.36, height - 0.55), (0.5, height - 0.15), (0.5, height)], 8, "stone", 2,
             phase=math.pi / 8, cap_top=False)
    return Kit(m, {1: STONE_DARK, 2: STONE})


def iron_chandelier(drop: float = 1.6) -> Kit:
    """An iron ring chandelier hanging on a chain, its six lanterns burning
    with a greenish potion-fire."""
    m = Mesh()
    for k in range(8):
        y0, y1 = -k * drop / 10, -(k + 1) * drop / 10
        mk.rod(m, (0.0, y0, 0.0), (0.0, y1, 0.0), 0.012 if k % 2 else 0.02, 4, "metal", 1, caps=False)
    ring_y = -drop
    for k in range(16):
        a0, a1 = 2 * math.pi * k / 16, 2 * math.pi * (k + 1) / 16
        mk.rod(m, (0.6 * math.sin(a0), ring_y, 0.6 * math.cos(a0)), (0.6 * math.sin(a1), ring_y, 0.6 * math.cos(a1)), 0.025,
               4, "metal", 1, caps=False)
    for k in range(3):
        a = 2 * math.pi * k / 3
        mk.rod(m, (0.0, -drop * 0.8, 0.0), (0.6 * math.sin(a), ring_y, 0.6 * math.cos(a)), 0.012, 4, "metal", 1, caps=False)
    for k in range(6):
        a = 2 * math.pi * k / 6 + 0.3
        x, z = 0.6 * math.sin(a), 0.6 * math.cos(a)
        mk.box(m, (x - 0.06, ring_y + 0.02, z - 0.06), (x + 0.06, ring_y + 0.04, z + 0.06), "metal", 1)
        mk.lathe(m, [(0.05, 0.0), (0.05, 0.14)], 6, "brew", 0, (x, ring_y + 0.04, z), cap_top=False)
        mk.lathe(m, [(0.065, 0.0), (0.0, 0.08)], 6, "metal", 1, (x, ring_y + 0.18, z))
    lights = [{"pos": [0.0, -drop - 0.1, 0.0], "color": "#A4E276", "intensity": 8, "range": 10.0}]
    return Kit(m, {1: IRON}, lights)


def dungeon_wall(width: float = 4.0, height: float = 5.4) -> Kit:
    """Ashlar facing for a dungeon wall: a grid of stone quads (one atlas
    tile each, so the masonry keeps its scale) over a deeper plinth course
    and under a moulded string course."""
    m = Mesh()
    step = 0.6
    nx = max(1, round(width / step))
    ny = max(1, round((height - 0.5) / step))
    dx, dy = width / nx, (height - 0.5) / ny
    for i in range(nx):
        for j in range(ny):
            x0, y0 = -width / 2 + i * dx, 0.5 + j * dy
            m.poly([(x0, y0, 0.012), (x0 + dx, y0, 0.012), (x0 + dx, y0 + dy, 0.012), (x0, y0 + dy, 0.012)], "stone",
                   zone=1 + (i * 7 + j * 3) % 2)
    for i in range(nx):
        x0 = -width / 2 + i * dx
        mk.box(m, (x0, 0.0, 0.0), (x0 + dx, 0.5, 0.09), "stone", 3, skip=("-z", "-y"))
    return Kit(m, {1: "#7C7264", 2: "#756B5E", 3: "#645D54"})


def dungeon_floor(width: float = 4.0, depth: float = 4.0) -> Kit:
    """Worn flagstones laid over a dungeon floor: a grid of stone quads a
    few millimetres up, alternating two tones."""
    m = Mesh()
    step = 0.8
    nx, nz = max(1, round(width / step)), max(1, round(depth / step))
    dx, dz = width / nx, depth / nz
    for i in range(nx):
        for j in range(nz):
            x0, z0 = -width / 2 + i * dx, -depth / 2 + j * dz
            _flat(m, [(x0, z0), (x0 + dx, z0), (x0 + dx, z0 + dz), (x0, z0 + dz)], 0.004, "stone", 1 + (i * 5 + j * 3) % 3)
    return Kit(m, {1: "#635D55", 2: "#6B6459", 3: "#5E5850"})


def wall_torch() -> Kit:
    """An iron torch bracket on a wall with a burning torch."""
    m = Mesh()
    mk.box(m, (-0.08, 1.7, 0.0), (0.08, 2.0, 0.03), "metal", 1)
    mk.rod(m, (0.0, 1.8, 0.03), (0.0, 1.95, 0.25), 0.015, 4, "metal", 1)
    mk.lathe(m, [(0.025, 0.0), (0.03, 0.3), (0.045, 0.34)], 6, "wood", 2, (0.0, 1.78, 0.22), cap_top=False, cap_bottom=True)
    FI.note(m, fires=[FI.fire((0.0, 2.1, 0.22), FI.cluster_tongues(0.04, 0.3, count=3, seed=17), embers=2)])
    lights = [{"pos": [0.0, 2.25, 0.3], "color": "#FFAE5C", "intensity": 6, "range": 7.0}]
    return Kit(m, {1: IRON, 2: AGED_OAK}, lights)


# ---------------------------------------------------------------------------
# Find-spot tells (flat greybox materials: no texture, no extra draw call in
# most cells). Small and quiet: something an attentive explorer notices.


def tell_glint() -> Kit:
    """Three tiny gold sparkles hanging in the air low over the floor."""
    m = Mesh()
    for (x, y, z, s) in ((0.0, 0.22, 0.0, 0.035), (0.09, 0.34, -0.05, 0.022), (-0.07, 0.15, 0.06, 0.018)):
        pts = [(x, y + s, z), (x + s * 0.35, y, z), (x, y - s, z), (x - s * 0.35, y, z)]
        for yaw in (0.0, math.pi / 2):
            q = [mk.add(mk.rot_y(mk.sub(p, (x, y, z)), yaw), (x, y, z)) for p in pts]
            m.poly(q, "g:glow_sun")
            m.poly(list(reversed(q)), "g:glow_sun")
    return Kit(m)


def tell_loose_stone() -> Kit:
    """One wall stone standing a few centimetres proud, a shade darker, with
    a thin crack of light along its top edge."""
    m = Mesh()
    mk.box(m, (-0.2, 0.32, 0.0), (0.2, 0.55, 0.05), "g:prop_stone_dark", skip=("-z",))
    mk.box(m, (-0.17, 0.552, 0.0), (0.12, 0.562, 0.02), "g:glow_sun", skip=("-z", "-y"))
    mk.box(m, (-0.22, 0.0, 0.0), (0.24, 0.04, 0.035), "g:prop_stone_dark", skip=("-z", "-y"))
    return Kit(m)


def tell_tapestry_corner() -> Kit:
    """The bottom corner of a wall hanging folded up, showing its lining and
    something that glints behind it."""
    m = Mesh()
    fold = [(-0.32, 0.25, 0.012), (0.0, 0.25, 0.012), (-0.02, 0.55, 0.05)]
    m.poly(fold, "g:prop_canvas")
    m.poly(list(reversed(fold)), "g:prop_canvas")
    lining = [(-0.32, 0.25, 0.008), (-0.02, 0.55, 0.045), (-0.36, 0.62, 0.008)]
    m.poly(lining, "g:fabric_house_a")
    m.poly(list(reversed(lining)), "g:fabric_house_a")
    for x, y, s in ((-0.2, 0.33, 0.018), (-0.12, 0.4, 0.012)):
        m.poly([(x, y + s, 0.004), (x + s * 0.4, y, 0.004), (x, y - s, 0.004), (x - s * 0.4, y, 0.004)], "g:glow_sun")
    return Kit(m)


def tell_rock_glint() -> Kit:
    """A lichen-dark rock with a gold glint caught at its foot (grounds)."""
    m = Mesh()
    mk.lathe(m, [(0.0, 0.0), (0.22, 0.02), (0.24, 0.12), (0.15, 0.24), (0.0, 0.27)], 6, "g:prop_stone_dark", 0,
             scale_xz=(1.0, 0.7), smooth=False)
    for x, z, s in ((0.24, 0.1, 0.03), (0.2, 0.18, 0.018)):
        for yaw in (0.0, math.pi / 2):
            pts = [(0.0, s, 0.0), (s * 0.35, 0.0, 0.0), (0.0, -s, 0.0), (-s * 0.35, 0.0, 0.0)]
            q = [mk.add(mk.rot_y(p, yaw), (x, 0.06, z)) for p in pts]
            m.poly(q, "g:glow_sun")
            m.poly(list(reversed(q)), "g:glow_sun")
    return Kit(m)


MODELS = {
    "owl_exchange_counter": owl_exchange_counter,
    "knitting_corner": knitting_corner,
    "transfiguration_table": transfiguration_table,
    "classroom_cauldron": classroom_cauldron,
    "potions_shelves": potions_shelves,
    "potions_blackboard": potions_blackboard,
    "teacher_desk": teacher_desk,
    "apothecary_cupboard": apothecary_cupboard,
    "barrels": barrels,
    "stone_column": stone_column,
    "iron_chandelier": iron_chandelier,
    "dungeon_wall": dungeon_wall,
    "dungeon_floor": dungeon_floor,
    "wall_torch": wall_torch,
    "tell_glint": tell_glint,
    "tell_loose_stone": tell_loose_stone,
    "tell_tapestry_corner": tell_tapestry_corner,
    "tell_rock_glint": tell_rock_glint,
}

# Triangle budgets (style guide section 6): hero props up to 8,000, station
# dressing and furniture-like pieces 3,000, small props and tells 400.
BUDGETS = {"owl_exchange_counter": 8000, "potions_shelves": 8000, "apothecary_cupboard": 3000, "knitting_corner": 3000,
           "transfiguration_table": 3000, "classroom_cauldron": 3000, "teacher_desk": 3000}
DEFAULT_BUDGET = 1500
TELL_BUDGET = 400


def build(name: str, **args) -> Kit:
    """The kit piece `name`, with optional keyword arguments (sizes)."""
    if name not in MODELS:
        raise KeyError(f"no kit model {name!r}")
    return MODELS[name](**args)


def budget(name: str) -> int:
    if name.startswith("tell_"):
        return TELL_BUDGET
    return BUDGETS.get(name, DEFAULT_BUDGET)


# The Dueling Club and the Charms classroom door (kit_school.py,
# which adds its models to MODELS and BUDGETS when it is imported).
import kit_school  # noqa: E402, F401


# ---------------------------------------------------------------------------
# The two grounds gates
# of the grounds classes (layout.json `greenhouse_gate`, `paddock_gate`).
# The grounds cells are not kitted, so these are props.json `kit` entries in
# flat greybox materials (`g:`: palette colours of layout_lib.MATERIALS, no
# texture and no new props-atlas slot): oak and the hedge's plant green.
# Origin on the ground at the gate's middle, the gate across x, its front
# +z (props.json turns it to face the castle, -z). The posts and the hedge
# reach 0.3 m below their origin, so they stand on the grounds' slope.

GATE_SINK = 0.3


def _hedge_block(x0: float, x1: float, height: float, depth: float, seed: int) -> list:
    """A clipped hedge between x0 and x1 (vegetation look A: the foliage kit's hedge pieces,
    world/foliage.py `hedge_run`, their feet GATE_SINK under the origin like the posts')."""
    return [FO.place(p, lambda q: (q[0] + x0, q[1], q[2])) for p in FO.hedge_run(x1 - x0, height, depth, seed)]


def garden_gate(width: float = 1.2, hedge: float = 2.6) -> Kit:
    """The Greenhouse Gate: a low oak wicket of pickets between two capped
    oak posts, a clipped hedge `hedge` m long either side (the greenhouses'
    garden boundary)."""
    m = Mesh()
    hw = width / 2
    plants = []
    for sx in (-1.0, 1.0):
        x = sx * (hw + 0.1)
        mk.box(m, (x - 0.1, -GATE_SINK, -0.1), (x + 0.1, 1.45, 0.1), "g:prop_wood", skip=("-y",))
        mk.box(m, (x - 0.13, 1.45, -0.13), (x + 0.13, 1.52, 0.13), "g:prop_wood", skip=("-y",))
        mk.lathe(m, [(0.07, 0.0), (0.08, 0.05), (0.0, 0.14)], 6, "g:prop_wood", 0, (x, 1.52, 0.0), cap_top=False)
        plants += _hedge_block(sx * (hw + 0.24) if sx > 0 else -(hw + 0.24 + hedge), sx * (hw + 0.24 + hedge) if sx > 0
                               else -(hw + 0.24), 1.25, 0.7, 3 if sx > 0 else 1)
    # The wicket: two rails, pickets with pointed tops, a brace.
    for y in (0.25, 0.8):
        mk.box(m, (-hw, y - 0.04, -0.03), (hw, y + 0.04, 0.03), "g:prop_wood")
    n = 7
    for i in range(n):
        x = -hw + 0.08 + i * (width - 0.16) / (n - 1)
        mk.prism(m, [(x - 0.04, 0.08), (x + 0.04, 0.08), (x + 0.04, 0.98), (x, 1.05), (x - 0.04, 0.98)], 0.03, 0.06,
                 "g:prop_wood")
    mk.rod(m, (-hw + 0.08, 0.28, 0.0), (hw - 0.08, 0.77, 0.0), 0.03, 4, "g:prop_wood", caps=False)
    return Kit(m, foliage=plants)


def field_gate(width: float = 3.2, fence: float = 3.0) -> Kit:
    """The Paddock Gate: a five-bar oak field gate with a diagonal brace
    between two stout posts, a short post-and-rail fence `fence` m long
    either side."""
    m = Mesh()
    hw = width / 2
    for sx in (-1.0, 1.0):
        x = sx * (hw + 0.13)
        mk.box(m, (x - 0.12, -GATE_SINK, -0.12), (x + 0.12, 1.6, 0.12), "g:prop_wood", skip=("-y",))
        mk.box(m, (x - 0.14, 1.6, -0.14), (x + 0.14, 1.66, 0.14), "g:prop_wood", skip=("-y",))
        # The fence: posts 1.5 m apart, three rails on their fronts.
        x0 = x + sx * 0.12
        bays = max(1, round(fence / 1.5))
        step = fence / bays
        for i in range(1, bays + 1):
            px = x0 + sx * i * step
            mk.box(m, (px - 0.07, -GATE_SINK, -0.07), (px + 0.07, 1.25, 0.07), "g:prop_wood", skip=("-y",))
        a, b = sorted((x0, x0 + sx * fence))
        for y in (0.35, 0.72, 1.1):
            mk.box(m, (a, y - 0.05, 0.07), (b, y + 0.05, 0.11), "g:prop_wood", skip=("-x", "+x"))
    # The gate: two stiles, five bars, the brace from the hinge foot to the latch head.
    for sx in (-1.0, 1.0):
        mk.box(m, (sx * hw - 0.06, 0.1, -0.05), (sx * hw + 0.06, 1.3, 0.05), "g:prop_wood")
    for k in range(5):
        y = 0.22 + k * 0.255
        mk.box(m, (-hw + 0.06, y - 0.045, -0.03), (hw - 0.06, y + 0.045, 0.03), "g:prop_wood", skip=("-x", "+x"))
    mk.rod(m, (-hw + 0.1, 0.25, 0.045), (hw - 0.1, 1.2, 0.045), 0.04, 4, "g:prop_wood", caps=False)
    return Kit(m)


MODELS.update({"garden_gate": garden_gate, "field_gate": field_gate})
BUDGETS.update({"garden_gate": 1500, "field_gate": 1500})
