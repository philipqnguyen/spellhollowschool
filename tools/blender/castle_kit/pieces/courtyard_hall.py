"""The Owl Exchange court's dressing in the Entrance Hall (castle M5c,
docs/art/castle-kit.md "The kitted cells"; open items 16-9 and 18-6): a free-standing
notice-board wall, crate stacks and a perch tree. The rugs are the room
furniture set's `kit_rug_<w>x<d>`.

Frames as props.json `kit`: origin at the bottom centre, yaw 0 facing +z.
All walk-through (the hall's furniture rule). Oak on the wood sheet
(castle_wood) and the props atlas's `oak` / `oak_aged`, cork, notices,
parcels, metals and the owls by palette name: no new draw in the hall
(castle_props and castle_wood). Invented content: the notices are blank
paper shapes (no text), the crates' markings plain bands."""

from __future__ import annotations

import math

import kit_models as km
import meshkit as mk
from castle_kit.pieces import hall
from castle_kit.pieces.geom import Piece
from castle_kit.pieces.hall_furniture import wbox

OAK, OAK_AGED, IRON, BRASS, CORK, LETTER, PARCEL, OWL_BROWN, OWL_PALE, STONE, RIBBON = 1, 2, 3, 4, 5, 6, 7, 8, 11, 12, 13
ZONES = {OAK: "oak", OAK_AGED: "oak_aged", IRON: "iron", BRASS: "brass", CORK: "cork", LETTER: "letter_white",
         PARCEL: "parcel_tan", OWL_BROWN: "owl_brown", OWL_PALE: "owl_pale", STONE: "beast_stone", RIBBON: "wine_red",
         90: "brass"}


def _poly(m: mk.Mesh, pts, tile: str, zone: int, normal) -> None:
    """A polygon wound to face `normal`."""
    n = mk.newell(pts)
    m.poly(pts if sum(a * b for a, b in zip(n, normal)) >= 0 else list(reversed(pts)), tile, zone=zone)


def _finish(p: Piece, m: mk.Mesh) -> Piece:
    p.faces.extend(hall.from_mesh(m, ZONES, walkthrough=True).faces)
    return p


# Notices on one face of the board: (x, y up the cork, w, h, zone); blank paper, no text.
NOTICES = ((-0.75, 0.12, 0.4, 0.52, LETTER), (-0.2, 0.5, 0.34, 0.42, PARCEL), (0.32, 0.1, 0.46, 0.34, LETTER),
           (0.72, 0.55, 0.3, 0.4, LETTER), (-0.62, 0.78, 0.36, 0.3, PARCEL), (0.15, 0.82, 0.3, 0.26, LETTER))


def notice_wall(width: float = 2.4) -> Piece:
    """`kit_notice_wall`: a free-standing oak screen `width` wide: two posts
    on cross feet, a framed cork board on each face (1.1-2.2 m up) with
    blank notices pinned, under a small gabled cap. Its faces at +-z. At
    most grid.NOTICE_WALL_TRIS."""
    p = Piece("", walkthrough=True)
    f = p.faces
    m = mk.Mesh()
    hw = width / 2.0
    y0, y1 = 0.95, 2.2
    for x in (-hw, hw):
        wbox(f, (x - 0.07, 0.0, -0.07), (x + 0.07, 2.45, 0.07), skip=("-y",), axis=1)
        wbox(f, (x - 0.06, 0.0, -0.4), (x + 0.06, 0.12, 0.4), skip=("-y",), axis=2)
    # The board: a frame round cork on both faces.
    bx0, bx1 = -hw + 0.07, hw - 0.07
    wbox(f, (bx0, y0 - 0.08, -0.05), (bx1, y0, 0.05), skip=("-x", "+x"))
    wbox(f, (bx0, y1, -0.05), (bx1, y1 + 0.08, 0.05), skip=("-x", "+x"))
    for sz in (1.0, -1.0):
        z = sz * 0.03
        pts = [(bx0, y0, z), (bx1, y0, z), (bx1, y1, z), (bx0, y1, z)]
        _poly(m, pts, "fabric", CORK, (0.0, 0.0, sz))
        for x, y, w, h, zone in NOTICES:
            xx = x * sz * (width - 0.4) / 2.0
            lo, hi = (xx - w / 2, y0 + y, min(z, z + sz * 0.008)), (xx + w / 2, y0 + y + h, max(z, z + sz * 0.008))
            mk.box(m, lo, hi, "fabric", zone, skip=("-z",) if sz > 0 else ("+z",))
            pin_lo = (xx - 0.015, y0 + y + h - 0.05, min(z + sz * 0.008, z + sz * 0.022))
            pin_hi = (xx + 0.015, y0 + y + h - 0.02, max(z + sz * 0.008, z + sz * 0.022))
            mk.box(m, pin_lo, pin_hi, "metal", BRASS, skip=("-z",) if sz > 0 else ("+z",))
    # The gabled cap over the board: two oak boards meeting at a ridge, closed at the ends.
    ya, yb, hz = 2.45, 2.72, 0.24
    xl, xr = -hw - 0.14, hw + 0.14
    for sz in (1.0, -1.0):
        top = [(xl, ya, sz * hz), (xr, ya, sz * hz), (xr, yb, 0.0), (xl, yb, 0.0)]
        _poly(m, top, "wood", OAK_AGED, (0.0, 1.0, sz * 0.9))
        _poly(m, [(x, y - 0.04, z) for x, y, z in top], "wood", OAK_AGED, (0.0, -1.0, -sz * 0.9))
    for x, sx in ((xl, -1.0), (xr, 1.0)):
        _poly(m, [(x, ya - 0.04, -hz), (x, ya - 0.04, hz), (x, yb, 0.0)], "wood", OAK_AGED, (sx, 0.0, 0.0))
    p.meta.update(width=width, height=2.72, depth=0.8)
    return _finish(p, m)


# A crate stack: (x, z, w, h, d, kind) with kind 0 a crate, 1 a parcel; stacked by y.
CRATE_SETS = (
    ((0.0, 0.0, 0.0, 0.9, 0.62, 0.7, 0), (0.95, 0.0, 0.05, 0.7, 0.5, 0.6, 0), (0.1, 0.62, -0.02, 0.7, 0.5, 0.6, 0),
     (0.15, 1.12, 0.05, 0.42, 0.24, 0.34, 1), (1.0, 0.5, 0.0, 0.36, 0.22, 0.3, 1)),
    ((0.0, 0.0, 0.0, 0.8, 0.56, 0.66, 0), (-0.85, 0.0, 0.1, 0.6, 0.44, 0.5, 0), (-0.05, 0.56, 0.0, 0.6, 0.46, 0.5, 0),
     (-0.85, 0.44, 0.1, 0.4, 0.24, 0.32, 1), (0.85, 0.0, 0.0, 0.46, 0.3, 0.4, 1)),
)


def crate_stack(seed: float = 0) -> Piece:
    """`kit_crate_stack`: crates (oak boards on the props atlas, darker
    corner battens) and string-tied parcels stacked as an owl-mail delivery
    waiting to be sorted; `seed` 0 or 1 picks the arrangement. At most
    grid.CRATE_STACK_TRIS."""
    k = int(round(seed)) % len(CRATE_SETS)
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    top = 0.0
    for x, y0, z, w, h, d, kind in CRATE_SETS[k]:
        y = y0
        lo, hi = (x - w / 2, y, z - d / 2), (x + w / 2, y + h, z + d / 2)
        if kind == 0:
            mk.box(m, lo, hi, "wood", OAK, skip=("-y",))
            # Battens round the front and back faces, a band across the lid.
            for zz, sk in ((hi[2], "-z"), (lo[2] - 0.02, "+z")):
                z0_, z1_ = zz, zz + 0.02
                mk.box(m, (lo[0], y + 0.04, z0_), (hi[0], y + 0.12, z1_), "wood", OAK_AGED, skip=(sk, "-y"))
                mk.box(m, (lo[0], y + h - 0.12, z0_), (hi[0], y + h - 0.04, z1_), "wood", OAK_AGED, skip=(sk, "-y"))
            mk.box(m, (x - 0.05, y + h, lo[2]), (x + 0.05, y + h + 0.015, hi[2]), "metal", IRON, skip=("-y",))
        else:
            mk.box(m, lo, hi, "fabric", PARCEL, skip=("-y",))
            mk.box(m, (x - 0.015, y + h, lo[2] - 0.005), (x + 0.015, y + h + 0.01, hi[2] + 0.005), "fabric", LETTER, skip=("-y",))
            mk.box(m, (lo[0] - 0.005, y + h, z - 0.015), (hi[0] + 0.005, y + h + 0.016, z + 0.015), "fabric", LETTER, skip=("-y",))
        top = max(top, y + h)
    xs = [x for x, *_ in CRATE_SETS[k]]
    p.meta.update(height=round(top, 4), seed=k, footprint=(min(xs) - 0.5, max(xs) + 0.5))
    return _finish(p, m)


def perch_tree(height: float = 3.0) -> Piece:
    """`kit_perch_tree`: an oak perch tree `height` (2.4-3.4 m) on a stone
    foot: a turned post with three tiers of cross perches turned 60 degrees
    apart, brass collars, a finial, and owls on the perches (our own design;
    kit_models' owl). At most grid.PERCH_TREE_TRIS."""
    if not 2.4 - 1e-9 <= height <= 3.4 + 1e-9:
        raise ValueError("kit_perch_tree: height must be 2.4-3.4 m")
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    mk.lathe(m, [(0.42, 0.0), (0.42, 0.12), (0.34, 0.2), (0.2, 0.28)], 8, "stone", STONE, cap_top=True)
    mk.lathe(m, [(0.09, 0.28), (0.075, height * 0.5), (0.065, height - 0.15)], 8, "wood", OAK_AGED, cap_top=False)
    mk.lathe(m, [(0.065, height - 0.15), (0.1, height - 0.08), (0.0, height)], 8, "wood", OAK_AGED, cap_top=False)
    tiers = (height - 1.4, height - 0.85, height - 0.35)
    reach = (0.75, 0.6, 0.45)
    owls = ((0, 1.0), (0, -1.0), (1, 1.0), (2, -1.0))
    for i, (y, r) in enumerate(zip(tiers, reach)):
        a = i * math.pi / 3
        dx, dz = math.cos(a), math.sin(a)
        mk.rod(m, (-r * dx, y, -r * dz), (r * dx, y, r * dz), 0.03, 6, "wood", OAK)
        mk.lathe(m, [(0.08, y - 0.05), (0.095, y), (0.08, y + 0.05)], 8, "metal", BRASS, cap_top=False)
        for sg in (-1.0, 1.0):
            mk.lathe(m, [(0.0, -0.03), (0.04, 0.0), (0.0, 0.03)], 6, "metal", BRASS, (sg * r * dx, y, sg * r * dz))
    for i, sg in owls:
        y, r = tiers[i], reach[i] * 0.72
        a = i * math.pi / 3
        base = (sg * r * math.cos(a), y + 0.02, sg * r * math.sin(a))
        km._owl(m, base, -a + (math.pi / 2 if sg > 0 else -math.pi / 2), OWL_BROWN if i != 1 else OWL_PALE, OWL_PALE,
                s=1.4)
    p.meta.update(height=height + 0.0, owls=len(owls))
    return _finish(p, m)

