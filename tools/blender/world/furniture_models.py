"""Furniture models for every furniture item in data/items/catalog.json
(pure Python, builds `meshkit.Mesh`; `build_furniture.py` exports them).

Each model is built from the item's catalog entry (footprint, height, top)
so it matches the placement rule exactly:

* floor and table items: origin at the bottom centre of the footprint,
  `footprint[0]` along X, `footprint[1]` along Z, front facing +Z;
* wall items: origin at the bottom centre of the back, which lies on z = 0,
  the item extending toward +Z (into the room).

Faces carry a dye `zone` (1 primary, 2 secondary, 3 trim, 0 undyed) and an
atlas tile as their material: `wood`, `fabric`, `metal`, `leather` (neutral,
tinted by the dye; `leaf` too) or `linen`, `canvas`, `books`, `glass`, `flame`, `brew`, `wax`,
`dark` (painted in their true colours, normally undyed). Tiles listed in
`FIT_TILES` take 0..1 UVs over the tile; the others take UVs in metres.
"""

from __future__ import annotations

import math

import meshkit as mk
from castle_kit import fire as FI
from meshkit import Mesh

PRIMARY, SECONDARY, TRIM, UNDYED = 1, 2, 3, 0
FIT_TILES = {"canvas", "books", "flame", "leaf", "brew"}


def _turned_leg(m: Mesh, x: float, z: float, y0: float, y1: float, r: float, zone: int, mat: str = "wood",
                segments: int = 6) -> None:
    """A simple turned leg: foot, waist, collar."""
    h = y1 - y0
    prof = [(r * 0.8, 0.0), (r, 0.06 * h), (r * 0.7, 0.35 * h), (r * 0.85, 0.6 * h), (r * 0.65, 0.85 * h), (r, h)]
    mk.lathe(m, prof, segments, mat, zone, (x, y0, z), cap_top=False)


def _pleats(m: Mesh, x0: float, x1: float, z: float, y0: float, y1: float, folds: int, depth: float, zone: int,
            mat: str = "fabric", facing: float = 1.0) -> None:
    """A gathered curtain: a pleated strip from x0 to x1 hanging at z."""
    pts = []
    for i in range(folds * 2 + 1):
        x = x0 + (x1 - x0) * i / (folds * 2)
        pts.append((x, z + (depth if i % 2 else 0.0) * facing))
    for (xa, za), (xb, zb) in zip(pts, pts[1:]):
        quad = [(xa, y0, za), (xb, y0, zb), (xb, y1, zb), (xa, y1, za)]
        # Both sides are seen (a curtain hangs free): add a back face too.
        m.poly(quad, mat, zone=zone)
        m.poly(list(reversed(quad)), mat, zone=zone)


# ---------------------------------------------------------------------------
# Floor items


def bed_four_poster(item: dict) -> Mesh:
    """Four posts with finials, panelled head and foot boards, a canopy with
    a fringed valance and gathered curtains at the corners."""
    w, d = item["furniture"]["footprint"]
    h = item["furniture"]["height"]
    m = Mesh()
    hw, hd = w / 2, d / 2
    post_r = 0.045
    for sx in (-1, 1):
        for sz in (-1, 1):
            x, z = sx * (hw - post_r), sz * (hd - post_r)
            prof = [(post_r * 1.3, 0.0), (post_r * 1.3, 0.12), (post_r, 0.16), (post_r * 0.8, 0.5),
                    (post_r, 0.9), (post_r * 0.75, 1.4), (post_r * 0.75, h - 0.32), (post_r * 1.1, h - 0.28),
                    (post_r * 0.9, h - 0.2)]
            mk.lathe(m, prof, 6, "wood", PRIMARY, (x, 0.0, z), cap_top=False)
            mk.lathe(m, [(post_r * 0.9, 0.0), (post_r * 1.2, 0.06), (post_r * 0.6, 0.15), (0.0, 0.2)], 6, "wood",
                     TRIM, (x, h - 0.2, z), cap_top=False)
    # Side and end rails, head and foot boards.
    mk.box(m, (-hw + 0.04, 0.22, -hd + 0.06), (-hw + 0.08, 0.42, hd - 0.06), "wood", PRIMARY)
    mk.box(m, (hw - 0.08, 0.22, -hd + 0.06), (hw - 0.04, 0.42, hd - 0.06), "wood", PRIMARY)
    mk.box(m, (-hw + 0.06, 0.22, -hd + 0.04), (hw - 0.06, 1.35, -hd + 0.09), "wood", PRIMARY)
    mk.box(m, (-hw + 0.06, 1.35, -hd + 0.03), (hw - 0.06, 1.42, -hd + 0.10), "wood", TRIM)
    mk.box(m, (-hw + 0.06, 0.22, hd - 0.09), (hw - 0.06, 0.78, hd - 0.04), "wood", PRIMARY)
    mk.box(m, (-hw + 0.06, 0.78, hd - 0.10), (hw - 0.06, 0.83, hd - 0.03), "wood", TRIM)
    # Mattress, coverlet (draped over the sides) and pillows.
    mk.box(m, (-hw + 0.09, 0.42, -hd + 0.10), (hw - 0.09, 0.62, hd - 0.10), "linen", UNDYED, skip=("-y",))
    # The coverlet is `fabric_cover` (the fabric tile in the atlas) so a
    # shell can give it its own material (a patchwork quilt) apart from the hangings.
    mk.box(m, (-hw + 0.08, 0.62, -hd + 0.55), (hw - 0.08, 0.68, hd - 0.10), "fabric_cover", SECONDARY, skip=("-y",))
    for sx in (-1, 1):
        edge = sx * (hw - 0.08)
        lo, hi = (edge, edge + 0.012) if sx > 0 else (edge - 0.012, edge)
        mk.box(m, (lo, 0.36, -hd + 0.55), (hi, 0.68, hd - 0.10), "fabric_cover", SECONDARY)
    mk.box(m, (-hw + 0.08, 0.36, hd - 0.122), (hw - 0.08, 0.68, hd - 0.10), "fabric_cover", SECONDARY)
    mk.box(m, (-hw + 0.08, 0.66, -hd + 0.53), (hw - 0.08, 0.70, -hd + 0.62), "fabric", TRIM)
    for sx in (-1, 1):
        mk.centred_box(m, (sx * w * 0.22, 0.62, -hd + 0.32), (w * 0.38, 0.13, 0.36), "linen", UNDYED, skip=("-y",))
    # Canopy: rails, top and a valance with a fringe.
    top = h - 0.24
    for z in (-hd + 0.02, hd - 0.02):
        mk.box(m, (-hw, top, z - 0.03), (hw, top + 0.06, z + 0.03), "wood", PRIMARY)
    for x in (-hw + 0.02, hw - 0.02):
        mk.box(m, (x - 0.03, top, -hd), (x + 0.03, top + 0.06, hd), "wood", PRIMARY)
    mk.box(m, (-hw + 0.01, top + 0.06, -hd + 0.01), (hw - 0.01, top + 0.08, hd - 0.01), "fabric", SECONDARY)
    val = 0.26
    for z in (-hd + 0.005, hd - 0.005):
        mk.box(m, (-hw, top - val + 0.05, z - 0.01), (hw, top + 0.07, z + 0.01), "fabric", SECONDARY)
        mk.box(m, (-hw, top - val, z - 0.016), (hw, top - val + 0.05, z + 0.016), "fabric", TRIM)
    for x in (-hw + 0.005, hw - 0.005):
        mk.box(m, (x - 0.01, top - val + 0.05, -hd), (x + 0.01, top + 0.07, hd), "fabric", SECONDARY)
        mk.box(m, (x - 0.016, top - val, -hd), (x + 0.016, top - val + 0.05, hd), "fabric", TRIM)
    # Gathered curtains at the four posts, tied back halfway down.
    cw = min(0.42, w * 0.28)
    for sx in (-1, 1):
        for sz in (-1, 1):
            x_out = sx * (hw - 0.02)
            x_in = x_out - sx * cw
            z = sz * (hd - 0.10)
            mark = m.mark()
            _pleats(m, min(x_in, x_out), max(x_in, x_out), 0.0, 0.12, top - 0.02, 3, 0.05, SECONDARY)
            m.place(mark, 0.0, (0.0, 0.0, z))
            mk.box(m, (min(x_in, x_out) + 0.03, 0.96, z - 0.01), (max(x_in, x_out) - 0.03, 0.99, z + 0.065), "fabric", TRIM)
    return m


def trunk_school(item: dict) -> Mesh:
    """A school trunk: oak body, flat lid (it doubles as a table), leather
    straps and handles, brass corners and lock."""
    w, d = item["furniture"]["footprint"]
    h = item["furniture"]["top"]
    m = Mesh()
    hw, hd = w / 2, d / 2
    mk.box(m, (-hw + 0.01, 0.0, -hd + 0.01), (hw - 0.01, h - 0.07, hd - 0.01), "wood", PRIMARY, skip=("-y",))
    # The lid sits 3 mm low so the straps over it end exactly at the top.
    mk.box(m, (-hw, h - 0.07, -hd), (hw, h - 0.003, hd), "wood", PRIMARY)
    mk.box(m, (-hw + 0.005, h - 0.085, -hd + 0.005), (hw - 0.005, h - 0.07, hd - 0.005), "wood", TRIM)
    for x in (-w * 0.27, w * 0.27):
        mk.box(m, (x - 0.03, 0.0, hd - 0.004), (x + 0.03, h, hd + 0.0), "leather", SECONDARY, skip=("-y",))
        mk.box(m, (x - 0.03, h - 0.003, -hd), (x + 0.03, h, hd), "leather", SECONDARY, skip=("-y",))
        mk.box(m, (x - 0.03, 0.0, -hd), (x + 0.03, h, -hd + 0.004), "leather", SECONDARY, skip=("-y",))
    # Brass corner caps, standing 3 mm proud of the corners.
    pad = 0.003
    for sx in (-1, 1):
        for sz in (-1, 1):
            x0, x1 = sorted((sx * (hw + pad), sx * (hw - 0.05)))
            z0, z1 = sorted((sz * (hd + pad), sz * (hd - 0.05)))
            for y0, y1 in ((0.0, 0.06), (h - 0.06, h)):
                mk.box(m, (x0, y0, z0), (x1, y1, z1), "metal", TRIM, skip=("-y",) if y0 == 0.0 else ())
    mk.box(m, (-0.05, h - 0.17, hd), (0.05, h - 0.06, hd + 0.008), "metal", TRIM)
    for sx in (-1, 1):
        x0, x1 = sorted((sx * hw, sx * (hw + 0.012)))
        mk.box(m, (x0, h * 0.55, -0.08), (x1, h * 0.62, 0.08), "leather", SECONDARY)
    return m


def table_bedside(item: dict) -> Mesh:
    """A bedside table: top, a drawer cabinet with a brass knob, turned legs
    and a low shelf."""
    w, d = item["furniture"]["footprint"]
    top = item["furniture"]["top"]
    m = Mesh()
    hw, hd = w / 2, d / 2
    mk.box(m, (-hw, top - 0.03, -hd), (hw, top, hd), "wood", PRIMARY)
    mk.box(m, (-hw + 0.03, top - 0.22, -hd + 0.03), (hw - 0.03, top - 0.03, hd - 0.03), "wood", PRIMARY, skip=("+y",))
    mk.box(m, (-hw + 0.05, top - 0.20, hd - 0.03), (hw - 0.05, top - 0.05, hd - 0.015), "wood", SECONDARY)
    mk.lathe(m, [(0.012, 0.0), (0.018, 0.01), (0.012, 0.025), (0.0, 0.03)], 6, "metal", TRIM,
             (0.0, top - 0.125, hd - 0.015), cap_top=False)
    for sx in (-1, 1):
        for sz in (-1, 1):
            _turned_leg(m, sx * (hw - 0.05), sz * (hd - 0.05), 0.0, top - 0.22, 0.022, PRIMARY)
    mk.box(m, (-hw + 0.04, 0.10, -hd + 0.04), (hw - 0.04, 0.13, hd - 0.04), "wood", PRIMARY)
    return m


def desk_writing(item: dict) -> Mesh:
    """A writing desk: leather-inlaid top, a drawer pedestal with brass pulls,
    turned legs and a low back gallery."""
    f = item["furniture"]
    w, d = f["footprint"]
    top, h = f["top"], f["height"]
    m = Mesh()
    hw, hd = w / 2, d / 2
    mk.box(m, (-hw, top - 0.035, -hd), (hw, top - 0.004, hd), "wood", PRIMARY)
    mk.box(m, (-hw + 0.06, top - 0.004, -hd + 0.06), (hw - 0.06, top, hd - 0.06), "leather", SECONDARY, skip=("-y",))
    mk.box(m, (-hw + 0.01, top - 0.004, -hd + 0.0), (hw - 0.01, h, -hd + 0.025), "wood", PRIMARY, skip=("-y",))
    ped = (-hw + 0.02, -hw + 0.42)
    mk.box(m, (ped[0], 0.0, -hd + 0.03), (ped[1], top - 0.035, hd - 0.03), "wood", PRIMARY, skip=("-y", "+y"))
    for i in range(3):
        y0 = 0.06 + i * (top - 0.12) / 3
        y1 = y0 + (top - 0.12) / 3 - 0.02
        mk.box(m, (ped[0] + 0.02, y0, hd - 0.03), (ped[1] - 0.02, y1, hd - 0.015), "wood", PRIMARY)
        mk.box(m, ((ped[0] + ped[1]) / 2 - 0.05, (y0 + y1) / 2 - 0.008, hd - 0.015),
               ((ped[0] + ped[1]) / 2 + 0.05, (y0 + y1) / 2 + 0.008, hd - 0.0), "metal", TRIM)
    mk.box(m, (-hw + 0.43, top - 0.13, hd - 0.05), (hw - 0.04, top - 0.035, hd - 0.03), "wood", PRIMARY)
    mk.box(m, (0.05, top - 0.09, hd - 0.03), (0.17, top - 0.075, hd - 0.012), "metal", TRIM)
    mk.box(m, (-hw + 0.43, 0.25, -hd + 0.03), (hw - 0.04, top - 0.035, -hd + 0.05), "wood", PRIMARY)
    for z in (-hd + 0.05, hd - 0.05):
        _turned_leg(m, hw - 0.05, z, 0.0, top - 0.035, 0.026, PRIMARY)
    return m


def chair_wooden(item: dict) -> Mesh:
    """A spindle-back chair: dyed seat, frame legs and back posts, spindles."""
    w, d = item["furniture"]["footprint"]
    h = item["furniture"]["height"]
    m = Mesh()
    hw, hd = w / 2, d / 2
    seat = 0.46
    mk.box(m, (-hw + 0.01, seat - 0.04, -hd + 0.04), (hw - 0.01, seat, hd), "wood", SECONDARY)
    for sx in (-1, 1):
        _turned_leg(m, sx * (hw - 0.04), hd - 0.05, 0.0, seat - 0.04, 0.02, PRIMARY)
        mk.lathe(m, [(0.022, 0.0), (0.022, h - 0.03), (0.026, h - 0.02), (0.0, h)], 6, "wood", PRIMARY,
                 (sx * (hw - 0.04), 0.0, -hd + 0.04), cap_top=False)
        mk.box(m, (sx * (hw - 0.04) - 0.012, 0.15, -hd + 0.06), (sx * (hw - 0.04) + 0.012, 0.18, hd - 0.07), "wood", PRIMARY)
    mk.box(m, (-hw + 0.03, h - 0.12, -hd + 0.02), (hw - 0.03, h - 0.04, -hd + 0.06), "wood", PRIMARY)
    mk.box(m, (-hw + 0.03, seat + 0.12, -hd + 0.025), (hw - 0.03, seat + 0.15, -hd + 0.055), "wood", PRIMARY)
    for i in range(4):
        x = -hw + 0.09 + i * (w - 0.18) / 3
        mk.rod(m, (x, seat + 0.15, -hd + 0.04), (x, h - 0.12, -hd + 0.04), 0.011, 5, "wood", TRIM, caps=False)
    return m


def armchair_velvet(item: dict) -> Mesh:
    """A wing-backed velvet armchair: rolled arms, a loose seat cushion,
    piping along the seams and short turned feet."""
    w, d = item["furniture"]["footprint"]
    h = item["furniture"]["height"]
    m = Mesh()
    hw, hd = w / 2, d / 2
    base0, seat = 0.12, 0.44
    mk.box(m, (-hw + 0.04, base0, -hd + 0.05), (hw - 0.04, seat - 0.08, hd - 0.02), "fabric", PRIMARY)
    mk.box(m, (-hw + 0.17, seat - 0.08, -hd + 0.2), (hw - 0.17, seat, hd - 0.03), "fabric", PRIMARY, skip=("-y",))
    mk.box(m, (-hw + 0.04, base0 - 0.01, hd - 0.03), (hw - 0.04, base0 + 0.02, hd - 0.01), "fabric", TRIM)
    # Back with a rounded top.
    mk.box(m, (-hw + 0.06, seat - 0.08, -hd + 0.02), (hw - 0.06, h - 0.1, -hd + 0.2), "fabric", PRIMARY, skip=("-y",))
    # The rounded top: a half-cylinder along x, built along z and turned.
    mark = m.mark()
    arc = [(0.09 * math.cos(math.pi * i / 6), 0.09 * math.sin(math.pi * i / 6)) for i in range(7)]
    mk.prism(m, arc, -(hw - 0.06), hw - 0.06, "fabric", PRIMARY)
    m.transform(mark, lambda p: (p[2], h - 0.1 + p[1], -hd + 0.11 - p[0]))
    # Arms with rolled tops.
    for sx in (-1, 1):
        x0, x1 = (sx * (hw - 0.17), sx * (hw - 0.02))
        lo, hi = min(x0, x1), max(x0, x1)
        mk.box(m, (lo, seat - 0.08, -hd + 0.15), (hi, 0.66, hd - 0.03), "fabric", PRIMARY, skip=("-y", "+y"))
        mark = m.mark()
        roll = [(0.085 * math.cos(math.pi * i / 6), 0.085 * math.sin(math.pi * i / 6)) for i in range(7)]
        mk.prism(m, roll, -hd + 0.15, hd - 0.03, "fabric", PRIMARY)
        m.transform(mark, lambda p, c=(lo + hi) / 2: (c + p[0], 0.66 + p[1] * 0.9, p[2]))
        mk.box(m, (lo + 0.01, 0.6, hd - 0.03), (hi - 0.01, 0.66, hd - 0.012), "fabric", TRIM)
    for sx in (-1, 1):
        for sz in (-1, 1):
            mk.lathe(m, [(0.03, 0.0), (0.02, 0.05), (0.032, 0.1), (0.032, base0)], 6, "wood", SECONDARY,
                     (sx * (hw - 0.08), 0.0, sz * (hd - 0.08)), cap_top=False)
    return m


def rug_round(item: dict) -> Mesh:
    """A round woven rug: concentric bands and an eight-pointed star."""
    w, _ = item["furniture"]["footprint"]
    h = item["furniture"]["height"]
    m = Mesh()
    r_out = w / 2
    segs = 32
    bands = [(0.0, 0.55, PRIMARY), (0.55, 0.62, SECONDARY), (0.62, 0.84, PRIMARY), (0.84, 0.90, SECONDARY),
             (0.90, 0.96, PRIMARY), (0.96, 1.0, TRIM)]

    def ring(r, y):
        return [(r * math.sin(2 * math.pi * i / segs), y, r * math.cos(2 * math.pi * i / segs)) for i in range(segs)]

    for r0, r1, zone in bands:
        a, b = ring(r0 * r_out, h), ring(r1 * r_out, h)
        for i in range(segs):
            j = (i + 1) % segs
            pts = [b[i], b[j], a[j], a[i]] if r0 > 0 else [b[i], b[j], a[0]]
            pts = pts if mk.newell(pts)[1] > 0 else list(reversed(pts))
            m.poly(pts, "fabric", zone=zone)
    edge = ring(r_out, h)
    low = ring(r_out, 0.0)
    for i in range(segs):
        j = (i + 1) % segs
        side = [low[i], low[j], edge[j], edge[i]]
        n = mk.newell(side)
        mid = mk.scale(mk.add(low[i], low[j]), 0.5)
        m.poly(side if mk.dot(n, mid) > 0 else list(reversed(side)), "fabric", zone=TRIM)
    star = []
    for i in range(16):
        r = (0.42 if i % 2 == 0 else 0.2) * r_out
        a = math.pi * i / 8
        star.append((r * math.sin(a), h + 0.0015, r * math.cos(a)))
    m.poly(star if mk.newell(star)[1] > 0 else list(reversed(star)), "fabric", zone=SECONDARY)
    return m


def bookcase_oak(item: dict) -> Mesh:
    """An oak bookcase: carcass, shelves, a gilt crown strip and rows of
    books (every third dyed with the secondary colour)."""
    w, d = item["furniture"]["footprint"]
    h = item["furniture"]["height"]
    m = Mesh()
    hw, hd = w / 2, d / 2
    t = 0.035
    for sx in (-1, 1):
        mk.box(m, (sx * hw - (t if sx > 0 else 0.0), 0.0, -hd), (sx * hw + (0.0 if sx > 0 else t), h - 0.06, hd), "wood", PRIMARY,
               skip=("-y",))
    mk.box(m, (-hw, h - 0.06, -hd), (hw, h, hd), "wood", PRIMARY)
    mk.box(m, (-hw + 0.005, h - 0.1, hd - 0.02), (hw - 0.005, h - 0.06, hd), "wood", TRIM)
    mk.box(m, (-hw + t, 0.0, -hd), (hw - t, h - 0.06, -hd + 0.02), "wood", PRIMARY, skip=("-y",))
    mk.box(m, (-hw + t, 0.0, hd - 0.03), (hw - t, 0.08, hd), "wood", PRIMARY, skip=("-y",))
    shelves = [0.08, 0.5, 0.92, 1.34, 1.76]
    for y in shelves:
        mk.box(m, (-hw + t, y, -hd + 0.02), (hw - t, y + 0.025, hd), "wood", PRIMARY, skip=("-y",) if y == shelves[0] else ())
    rng = _Rand(7)
    for k, y in enumerate(shelves[:-1]):
        x = -hw + t + 0.01
        n = 0
        limit = hw - t - 0.01 - (0.18 if k % 2 else 0.0)
        while x < limit:
            bw = rng.uniform(0.025, 0.06)
            bh = rng.uniform(0.24, 0.36)
            if x + bw > limit:
                break
            zone = SECONDARY if n % 3 == 1 else UNDYED
            mat = "leather" if zone else "books"
            y0 = y + 0.025
            pts_front = [(x, y0, hd - 0.03), (x + bw, y0, hd - 0.03), (x + bw, y0 + bh, hd - 0.03), (x, y0 + bh, hd - 0.03)]
            u0 = rng.uniform(0.0, 0.85)
            uvs = [(u0, 0.0), (u0 + 0.12, 0.0), (u0 + 0.12, 1.0), (u0, 1.0)]
            m.poly(pts_front, mat, uvs if mat == "books" else None, zone)
            # Covers and page tops (the spine is the front face above).
            side_mat = "leather" if zone else "dark"
            mk.box(m, (x, y0, -hd + 0.06), (x + bw, y0 + bh, hd - 0.03), side_mat, zone, skip=("-y", "+z", "-z"))
            x += bw + 0.002
            n += 1
    return m


class _Rand:
    """Small deterministic generator (models must not change between runs)."""

    def __init__(self, seed: int):
        self.s = seed * 2654435761 % 2**32 or 1

    def uniform(self, a: float, b: float) -> float:
        self.s ^= (self.s << 13) & 0xFFFFFFFF
        self.s ^= self.s >> 17
        self.s ^= (self.s << 5) & 0xFFFFFFFF
        return a + (b - a) * (self.s / 2**32)


# ---------------------------------------------------------------------------
# Wall items


SHIELD = [(-0.16, 0.18), (0.16, 0.18), (0.16, 0.0), (0.12, -0.12), (0.0, -0.2), (-0.12, -0.12), (-0.16, 0.0)]


def _charge(slot: str) -> list[list[tuple[float, float]]]:
    """A simple heraldic charge for each house slot (our own abstract
    designs, not the houses' crests): chevron, roundels, star, wave."""
    if slot == "house_a":
        return [[(-0.13, -0.08), (0.0, 0.08), (0.13, -0.08), (0.13, -0.02), (0.0, 0.14), (-0.13, -0.02)]]
    if slot == "house_b":
        out = []
        for cx, cy in ((-0.07, 0.07), (0.07, 0.07), (0.0, -0.06)):
            out.append([(cx + 0.045 * math.cos(2 * math.pi * i / 10), cy + 0.045 * math.sin(2 * math.pi * i / 10)) for i in range(10)])
        return out
    if slot == "house_c":
        pts = []
        for i in range(16):
            r = 0.12 if i % 2 == 0 else 0.05
            a = math.pi / 2 + math.pi * i / 8
            pts.append((r * math.cos(a), 0.01 + r * math.sin(a)))
        return [pts]
    if slot == "house_d":
        band = []
        n = 10
        for i in range(n + 1):
            x = -0.13 + 0.26 * i / n
            band.append((x, 0.05 * math.sin(i / n * 2 * math.pi) - 0.025))
        top = [(x, y + 0.05) for x, y in reversed(band)]
        return [band + top]
    raise ValueError(slot)


def _flat_shape(m: Mesh, outline, z: float, zone: int, mat: str = "fabric", y0: float = 0.0, x0: float = 0.0) -> None:
    pts = [(x0 + x, y0 + y, z) for x, y in outline]
    if mk.newell(pts)[2] < 0:
        pts.reverse()
    m.poly(pts, mat, zone=zone)


def banner_house(item: dict) -> Mesh:
    """A house banner: a pointed pennant on a rod with finials, a fringed
    border, and a shield bearing a simple charge."""
    f = item["furniture"]
    w, h = f["footprint"][0], f["height"]
    slot = item["asset"].rsplit("_", 2)[-2] + "_" + item["asset"].rsplit("_", 1)[-1]
    m = Mesh()
    rod_y = h - 0.05
    mk.rod(m, (-w / 2 + 0.03, rod_y, 0.03), (w / 2 - 0.03, rod_y, 0.03), 0.014, 6, "metal", TRIM)
    for sx in (-1, 1):
        mk.lathe(m, [(0.022, 0.0), (0.028, 0.012), (0.0, 0.03)], 6, "metal", TRIM, (sx * (w / 2 - 0.03), rod_y - 0.015, 0.03))
        mk.box(m, (sx * (w / 2 - 0.12) - 0.008, rod_y - 0.02, 0.0), (sx * (w / 2 - 0.12) + 0.008, rod_y + 0.02, 0.03), "metal", TRIM)
    cw = w - 0.12
    top = rod_y - 0.02
    # Counter-clockwise seen from +Z: down the left edge, to the point, up the right.
    cloth = [(-cw / 2, top), (-cw / 2, 0.28), (0.0, 0.02), (cw / 2, 0.28), (cw / 2, top)]
    mk.prism(m, cloth, 0.02, 0.03, "fabric", PRIMARY)
    border = mk.offset_convex(cloth, -0.035)
    for i in range(len(cloth)):
        j = (i + 1) % len(cloth)
        if abs(cloth[i][1] - top) < 1e-6 and abs(cloth[j][1] - top) < 1e-6:
            continue
        quad = [(cloth[i][0], cloth[i][1], 0.0305), (cloth[j][0], cloth[j][1], 0.0305),
                (border[j][0], border[j][1], 0.0305), (border[i][0], border[i][1], 0.0305)]
        if mk.newell(quad)[2] < 0:
            quad.reverse()
        m.poly(quad, "fabric", zone=TRIM)
    sy = 0.95 * (h / 1.6)
    _flat_shape(m, SHIELD, 0.031, SECONDARY, y0=sy)
    for shape in _charge(slot):
        _flat_shape(m, shape, 0.032, PRIMARY, y0=sy)
    mk.lathe(m, [(0.0, 0.0), (0.02, 0.03), (0.012, 0.09), (0.006, 0.1)], 6, "fabric", TRIM, (0.0, -0.08 + 0.1 - 0.02, 0.025))
    return m


def _arc_strip(m: Mesh, cx: float, cy: float, r: float, t: float, a0: float, a1: float, n: int, z: float,
               zone: int, mat: str = "fabric") -> None:
    """A flat curved strip (a ring segment from angle a0 to a1, `t` wide) at z."""
    for i in range(n):
        u0, u1 = a0 + (a1 - a0) * i / n, a0 + (a1 - a0) * (i + 1) / n
        quad = [(cx + (r - t / 2) * math.cos(u0), cy + (r - t / 2) * math.sin(u0), z),
                (cx + (r + t / 2) * math.cos(u0), cy + (r + t / 2) * math.sin(u0), z),
                (cx + (r + t / 2) * math.cos(u1), cy + (r + t / 2) * math.sin(u1), z),
                (cx + (r - t / 2) * math.cos(u1), cy + (r - t / 2) * math.sin(u1), z)]
        if mk.newell(quad)[2] < 0:
            quad.reverse()
        m.poly(quad, mat, zone=zone)


def pennant_house_cup(item: dict) -> Mesh:
    """The House Cup Pennant (mailed to the winning
    house's members, wall furniture, not sold): a pointed felt pennant in
    the house's primary colour (`house_dyes`) with a gold (trim) border,
    a band of the secondary colour under a gilt rod with finials and a
    hanging cord, a two-handled cup in the secondary colour on the field, and
    a gold tassel at the point. Our own design (no lettering)."""
    f = item["furniture"]
    w, h = f["footprint"][0], f["height"]
    m = Mesh()
    # Rod, finials and the cord to a hook at the top.
    rod_y = h - 0.075
    mk.rod(m, (-w / 2 + 0.03, rod_y, 0.03), (w / 2 - 0.03, rod_y, 0.03), 0.011, 6, "metal", TRIM)
    for sx in (-1, 1):
        mk.lathe(m, [(0.018, 0.0), (0.024, 0.012), (0.0, 0.03)], 6, "metal", TRIM, (sx * (w / 2 - 0.03), rod_y - 0.012, 0.03))
        mk.rod(m, (sx * (w / 2 - 0.09), rod_y + 0.008, 0.03), (0.0, h - 0.012, 0.03), 0.004, 4, "fabric", TRIM)
    mk.lathe(m, [(0.0, 0.0), (0.012, 0.006), (0.0, 0.012)], 6, "metal", TRIM, (0.0, h - 0.014, 0.03))
    # The pennant: square shoulders, pointed foot (convex), on the rod.
    cw = w - 0.12
    top = rod_y - 0.012
    shoulder, point = 0.44, 0.075
    cloth = [(-cw / 2, top), (-cw / 2, shoulder), (0.0, point), (cw / 2, shoulder), (cw / 2, top)]
    mk.prism(m, cloth, 0.022, 0.032, "fabric", PRIMARY)
    border = mk.offset_convex(cloth, -0.026)
    for i in range(len(cloth)):
        j = (i + 1) % len(cloth)
        if abs(cloth[i][1] - top) < 1e-6 and abs(cloth[j][1] - top) < 1e-6:
            continue
        quad = [(cloth[i][0], cloth[i][1], 0.0325), (cloth[j][0], cloth[j][1], 0.0325),
                (border[j][0], border[j][1], 0.0325), (border[i][0], border[i][1], 0.0325)]
        if mk.newell(quad)[2] < 0:
            quad.reverse()
        m.poly(quad, "fabric", zone=TRIM)
    # The band of the secondary colour under the rod.
    bw = cw / 2 - 0.026
    _flat_shape(m, [(-bw, top - 0.09), (bw, top - 0.09), (bw, top - 0.045), (-bw, top - 0.045)], 0.033, SECONDARY)
    # The cup: a round bowl, two handles, a stem with a knop, a stepped foot.
    cy = 0.62
    bowl = [(-0.085, cy + 0.07), (-0.082, cy + 0.03)] + [
        (0.085 * math.cos(a), cy + 0.02 + 0.085 * math.sin(a)) for a in
        [math.pi + math.pi * k / 8 for k in range(1, 8)]] + [(0.082, cy + 0.03), (0.085, cy + 0.07)]
    _flat_shape(m, bowl, 0.034, SECONDARY)
    _flat_shape(m, [(-0.1, cy + 0.07), (0.1, cy + 0.07), (0.1, cy + 0.085), (-0.1, cy + 0.085)], 0.034, SECONDARY)
    for sx in (-1, 1):
        _arc_strip(m, sx * 0.085, cy + 0.035, 0.035, 0.014, -math.pi / 2 if sx > 0 else math.pi / 2,
                   math.pi / 2 if sx > 0 else 3 * math.pi / 2, 5, 0.034, SECONDARY)
    _flat_shape(m, [(-0.014, cy - 0.12), (0.014, cy - 0.12), (0.014, cy - 0.066), (-0.014, cy - 0.066)], 0.034, SECONDARY)
    _flat_shape(m, [(-0.03, cy - 0.105), (0.03, cy - 0.105), (0.03, cy - 0.09), (-0.03, cy - 0.09)], 0.035, SECONDARY)
    _flat_shape(m, [(-0.07, cy - 0.15), (0.07, cy - 0.15), (0.045, cy - 0.12), (-0.045, cy - 0.12)], 0.034, SECONDARY)
    # A gold star over the cup.
    star = []
    for k in range(10):
        a = math.pi / 2 + math.pi * k / 5
        r = 0.03 if k % 2 == 0 else 0.013
        star.append((r * math.cos(a), cy + 0.15 + r * math.sin(a)))
    for k in range(5):  # five convex kites, so no concave polygon
        kite = [(0.0, cy + 0.15), star[(2 * k - 1) % 10], star[2 * k], star[2 * k + 1]]
        _flat_shape(m, kite, 0.034, TRIM)
    # The tassel at the point.
    mk.lathe(m, [(0.0, 0.0), (0.016, 0.004), (0.02, 0.04), (0.009, 0.052), (0.012, 0.065), (0.0, 0.075)], 6, "fabric",
             TRIM, (0.0, point - 0.075, 0.027), cap_top=False)
    return m


def painting_landscape(item: dict) -> Mesh:
    """A gilt-framed Highland landscape: bevelled frame, a slip, the canvas."""
    f = item["furniture"]
    w, d = f["footprint"]
    h = f["height"]
    m = Mesh()
    fw = 0.075
    # Frame bars as bevelled prisms (front narrower than back).
    outer = [(-w / 2, 0.0), (w / 2, 0.0), (w / 2, h), (-w / 2, h)]
    inner = [(-w / 2 + fw, fw), (w / 2 - fw, fw), (w / 2 - fw, h - fw), (-w / 2 + fw, h - fw)]
    for i in range(4):
        j = (i + 1) % 4
        o0, o1, i0, i1 = outer[i], outer[j], inner[i], inner[j]
        mid_o = [(o0[0] * 0.97, o0[1] + (h / 2 - o0[1]) * 0.03), (o1[0] * 0.97, o1[1] + (h / 2 - o1[1]) * 0.03)]
        front = [(i0[0], i0[1], d * 0.75), (i1[0], i1[1], d * 0.75), (mid_o[1][0], mid_o[1][1], d), (mid_o[0][0], mid_o[0][1], d)]
        for poly in (
            [(o0[0], o0[1], 0.0), (o1[0], o1[1], 0.0), (o1[0], o1[1], d * 0.8), (o0[0], o0[1], d * 0.8)],
            [(o0[0], o0[1], d * 0.8), (o1[0], o1[1], d * 0.8), (mid_o[1][0], mid_o[1][1], d), (mid_o[0][0], mid_o[0][1], d)],
            front,
        ):
            n = mk.newell(poly)
            c = tuple(sum(p[k] for p in poly) / 4 for k in range(3))
            away = mk.sub(c, (0.0, h / 2, -0.5))
            m.poly(poly if mk.dot(n, away) > 0 else list(reversed(poly)), "metal", zone=PRIMARY)
    s = fw + 0.018
    slip = [(-w / 2 + s, s), (w / 2 - s, s), (w / 2 - s, h - s), (-w / 2 + s, h - s)]
    for i in range(4):
        j = (i + 1) % 4
        quad = [(inner[i][0], inner[i][1], d * 0.75), (inner[j][0], inner[j][1], d * 0.75),
                (slip[j][0], slip[j][1], d * 0.55), (slip[i][0], slip[i][1], d * 0.55)]
        if mk.newell(quad)[2] < 0:
            quad.reverse()
        m.poly(quad, "wood", zone=SECONDARY)
    canvas = [(x, y, d * 0.55) for x, y in slip]
    u = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    m.poly(canvas, "canvas", u, UNDYED)
    return m


# ---------------------------------------------------------------------------
# Table items


def cage_owl(item: dict) -> Mesh:
    """A domed owl cage with a perch and a small snowy owl inside."""
    f = item["furniture"]
    w = f["footprint"][0]
    h = f["height"]
    m = Mesh()
    r = w / 2 - 0.01
    mk.lathe(m, [(r, 0.0), (r, 0.03), (r * 0.94, 0.045), (r * 0.9, 0.05)], 12, "metal", SECONDARY)
    bars = 12
    bar_top = h * 0.72
    for i in range(bars):
        a = 2 * math.pi * i / bars
        x, z = r * 0.88 * math.sin(a), r * 0.88 * math.cos(a)
        mk.rod(m, (x, 0.05, z), (x, bar_top, z), 0.004, 4, "metal", PRIMARY, caps=False)
    mk.lathe(m, [(r * 0.92, 0.0), (r * 0.92, 0.02)], 12, "metal", PRIMARY, (0.0, bar_top - 0.01, 0.0), cap_top=False)
    dome = [(r * 0.9 * math.cos(math.pi / 2 * i / 4), (h * 0.9 - bar_top) * math.sin(math.pi / 2 * i / 4)) for i in range(5)]
    mk.lathe(m, [(x, y) for x, y in dome], 12, "metal", PRIMARY, (0.0, bar_top + 0.01, 0.0), cap_top=False)
    mk.lathe(m, [(0.004, 0.0), (0.03, 0.02), (0.03, 0.04), (0.004, 0.06)], 6, "metal", PRIMARY,
             (0.0, h - 0.06, 0.0), cap_top=False, cap_bottom=False)
    mk.rod(m, (-r * 0.85, h * 0.32, 0.0), (r * 0.85, h * 0.32, 0.0), 0.008, 5, "wood", TRIM)
    # A small snowy owl on the perch.
    oy = h * 0.32 + 0.005
    mk.lathe(m, [(0.0, 0.0), (0.035, 0.01), (0.045, 0.05), (0.04, 0.09), (0.0, 0.11)], 8, "linen", UNDYED,
             (0.0, oy, 0.0), scale_xz=(1.0, 0.85))
    mk.lathe(m, [(0.0, 0.0), (0.032, 0.015), (0.03, 0.045), (0.0, 0.06)], 8, "linen", UNDYED,
             (0.0, oy + 0.1, 0.004), scale_xz=(1.0, 0.85))
    for sx in (-1, 1):
        mk.centred_box(m, (sx * 0.012, oy + 0.135, 0.028), (0.012, 0.012, 0.004), "dark", UNDYED)
    return m


def lantern_candle(item: dict) -> Mesh:
    """A square candle lantern: corner posts and glazing bars, a pyramid cap,
    a ring handle, and a lit candle (the flame is emissive)."""
    f = item["furniture"]
    w = f["footprint"][0]
    h = f["height"]
    m = Mesh()
    hw = w / 2
    body = h * 0.72
    mk.box(m, (-hw, 0.0, -hw), (hw, 0.025, hw), "metal", PRIMARY, skip=("-y",))
    mk.box(m, (-hw + 0.01, body - 0.02, -hw + 0.01), (hw - 0.01, body, hw - 0.01), "metal", PRIMARY)
    for sx in (-1, 1):
        for sz in (-1, 1):
            mk.box(m, (sx * (hw - 0.012) - 0.007, 0.025, sz * (hw - 0.012) - 0.007),
                   (sx * (hw - 0.012) + 0.007, body - 0.02, sz * (hw - 0.012) + 0.007), "metal", PRIMARY, skip=("-y", "+y"))
    # Open sides with a glazing bar across each (the glass itself is left
    # out so the candle shows without a transparent material).
    g = hw - 0.012
    ymid = body * 0.55
    for sx in (-1, 1):
        mk.box(m, (-g, ymid - 0.004, sx * g - 0.003), (g, ymid + 0.004, sx * g + 0.003), "metal", PRIMARY)
        mk.box(m, (sx * g - 0.003, ymid - 0.004, -g), (sx * g + 0.003, ymid + 0.004, g), "metal", PRIMARY)
    roof_top = h - 0.05
    apex = (0.0, roof_top, 0.0)
    corners = [(-hw + 0.005, body, -hw + 0.005), (hw - 0.005, body, -hw + 0.005), (hw - 0.005, body, hw - 0.005),
               (-hw + 0.005, body, hw - 0.005)]
    for i in range(4):
        tri = [corners[i], apex, corners[(i + 1) % 4]]
        c = tuple(sum(p[k] for p in tri) / 3 for k in range(3))
        n = mk.newell(tri)
        m.poly(tri if mk.dot(n, mk.sub(c, (0.0, body, 0.0))) > 0 else list(reversed(tri)), "metal", zone=PRIMARY)
    mk.lathe(m, [(0.005, 0.0), (0.025, 0.012), (0.03, 0.03), (0.025, 0.045), (0.005, 0.05)], 8, "metal", TRIM,
             (0.0, roof_top, 0.0), cap_top=False)
    mk.cylinder(m, (0.0, 0.025, 0.0), 0.022, h * 0.32, 8, "wax", UNDYED)
    fy = 0.025 + h * 0.32
    FI.note(m, candles=[(0.0, fy, 0.0, 0.06)])  # the flame: the client's animated fire (docs/architecture.md)
    return m


# ---------------------------------------------------------------------------
# The basic owl-order furniture (a brewing cauldron, a stool,
# plants, a candelabra, a side table)


def _double_sided(m: Mesh, pts, mat: str, zone: int, uvs=None) -> None:
    """A thin card seen from both sides (leaves, fronds)."""
    m.poly(pts, mat, uvs, zone)
    m.poly(list(reversed(pts)), mat, list(reversed(uvs)) if uvs else None, zone)


def cauldron(r_belly: float, base_y: float, rim_y: float, m: Mesh, zone: int = PRIMARY, brew: bool = True,
             segments: int = 14) -> float:
    """A pot-bellied cauldron from `base_y` to its rolled rim at `rim_y`,
    with a brew surface just below the rim. Returns the rim radius."""
    h = rim_y - base_y
    r_rim = r_belly * 0.86
    outside = [(r_belly * 0.45, 0.0), (r_belly * 0.82, 0.08 * h), (r_belly, 0.38 * h), (r_belly * 0.96, 0.7 * h),
               (r_rim * 0.98, 0.9 * h), (r_rim, h), (r_rim * 1.06, h + 0.012), (r_rim * 0.98, h + 0.02)]
    mk.lathe(m, outside, segments, "metal", zone, (0.0, base_y, 0.0), cap_top=False, cap_bottom=True)
    # The inner wall down to the brew.
    inner = [(r_rim * 0.98, h + 0.02), (r_rim * 0.93, h - 0.005), (r_rim * 0.92, h - 0.06)]
    mk.lathe(m, list(reversed([(r, y) for r, y in inner])), segments, "dark", UNDYED, (0.0, base_y, 0.0), cap_top=False)
    if brew:
        ring = [(r_rim * 0.92 * math.sin(2 * math.pi * i / segments), base_y + h - 0.06,
                 r_rim * 0.92 * math.cos(2 * math.pi * i / segments)) for i in range(segments)]
        uvs = [(0.5 + 0.5 * math.sin(2 * math.pi * i / segments), 0.5 + 0.5 * math.cos(2 * math.pi * i / segments))
               for i in range(segments)]
        m.poly(ring if mk.newell(ring)[1] > 0 else list(reversed(ring)), "brew",
               uvs if mk.newell(ring)[1] > 0 else list(reversed(uvs)), UNDYED)
    return r_rim


def cauldron_pewter(item: dict) -> Mesh:
    """A pewter cauldron for brewing at home: a square iron hearth plate with
    a glowing fire basket, the cauldron on three feet over it, ring handles
    and a stirring rod resting in the brew."""
    f = item["furniture"]
    w, d = f["footprint"]
    h = f["height"]
    m = Mesh()
    hw, hd = w / 2, d / 2
    # Hearth plate and its feet (iron, secondary).
    mk.box(m, (-hw + 0.02, 0.025, -hd + 0.02), (hw - 0.02, 0.05, hd - 0.02), "metal", SECONDARY)
    for sx in (-1, 1):
        for sz in (-1, 1):
            mk.box(m, (sx * (hw - 0.06) - 0.03, 0.0, sz * (hd - 0.06) - 0.03), (sx * (hw - 0.06) + 0.03, 0.025,
                   sz * (hd - 0.06) + 0.03), "metal", SECONDARY, skip=("-y",))
    # The fire basket with glowing embers.
    mk.lathe(m, [(0.12, 0.0), (0.16, 0.07), (0.17, 0.08)], 10, "metal", SECONDARY, (0.0, 0.05, 0.0), cap_top=False,
             cap_bottom=True)
    ember = [(0.155 * math.sin(2 * math.pi * i / 10), 0.12, 0.155 * math.cos(2 * math.pi * i / 10)) for i in range(10)]
    uvs = [(0.5 + 0.4 * math.sin(2 * math.pi * i / 10), 0.35 + 0.3 * math.cos(2 * math.pi * i / 10)) for i in range(10)]
    m.poly(ember if mk.newell(ember)[1] > 0 else list(reversed(ember)), "flame",
           uvs if mk.newell(ember)[1] > 0 else list(reversed(uvs)), UNDYED)
    # The flames licking up round the cauldron: the client's animated fire (docs/architecture.md).
    FI.note(m, fires=[FI.fire((0.0, 0.115, 0.0), FI.cluster_tongues(0.08, 0.11, count=3, seed=23), embers=1)])
    # The cauldron on three feet.
    base_y = 0.2
    rim_y = 0.6
    r_belly = hw - 0.065
    for k in range(3):
        a = 2 * math.pi * k / 3 + 0.5
        x, z = r_belly * 0.5 * math.sin(a), r_belly * 0.5 * math.cos(a)
        mk.rod(m, (x * 1.5, 0.05, z * 1.5), (x, base_y + 0.03, z), 0.018, 5, "metal", PRIMARY)
    r_rim = cauldron(r_belly, base_y, rim_y, m)
    # Ring handles on lugs at either side (trim).
    for sx in (-1, 1):
        cx = sx * (r_rim * 1.04 + 0.01)
        mk.box(m, (min(cx, sx * r_rim * 0.95), rim_y - 0.05, -0.02), (max(cx, sx * r_rim * 0.95), rim_y - 0.02, 0.02),
               "metal", TRIM)
        pts = []
        for i in range(9):
            t = math.pi * i / 8
            pts.append((cx + sx * 0.045 * math.sin(t), rim_y - 0.035 - 0.07 * math.sin(t) ** 1.2, 0.045 * math.cos(t)))
        for p0, p1 in zip(pts, pts[1:]):
            mk.rod(m, p0, p1, 0.008, 4, "metal", TRIM, caps=False)
    # A stirring rod leaning on the rim.
    mk.rod(m, (0.03, rim_y - 0.08, 0.02), (-r_rim * 0.75, h - 0.01, -r_rim * 0.4), 0.012, 5, "wood", TRIM)
    return m


def stool_three_leg(item: dict) -> Mesh:
    """A milking-stool style seat: a thick round top, three splayed legs
    socketed through it and a ring stretcher."""
    f = item["furniture"]
    w = f["footprint"][0]
    h = f["height"]
    m = Mesh()
    r = w / 2
    # The seat's wood stops `proud` under the catalog top (= height): the
    # wedged tenons stand that proud of it, their ends the top itself, so
    # nothing rises above the top a table item stands on.
    proud = 0.002
    s = h - proud
    mk.lathe(m, [(r * 0.92, s - 0.045), (r, s - 0.035), (r, s - 0.01), (r * 0.95, s)], 14, "wood", PRIMARY,
             cap_top=True, cap_bottom=True)
    ends = []
    for k in range(3):
        a = 2 * math.pi * k / 3
        top = (r * 0.5 * math.sin(a), s - 0.04, r * 0.5 * math.cos(a))
        foot = (r * 0.82 * math.sin(a), 0.012, r * 0.82 * math.cos(a))
        mk.rod(m, foot, top, 0.022, 6, "wood", PRIMARY)
        # Wedged tenon showing on the seat, its end the top.
        mk.lathe(m, [(0.016, 0.0), (0.016, proud + 0.001)], 6, "wood", TRIM, (top[0], s - 0.001, top[2]))
        ends.append(mk.lerp(foot, top, 0.32))
    for i in range(3):
        mk.rod(m, ends[i], ends[(i + 1) % 3], 0.012, 5, "wood", TRIM)
    return m


def _clay_pot(m: Mesh, r_top: float, r_bot: float, height: float, zone: int, base: tuple = (0.0, 0.0, 0.0)) -> None:
    """A terracotta pot with a rolled rim and a soil top."""
    mk.lathe(m, [(r_bot, 0.0), (r_bot * 1.02, 0.02), (r_top * 0.92, height * 0.82), (r_top, height * 0.84),
                 (r_top, height), (r_top * 0.92, height)], 12, "leather", zone, base, cap_top=False, cap_bottom=True)
    mk.lathe(m, [(r_top * 0.92, height), (r_top * 0.9, height - 0.02)], 12, "dark", UNDYED, base, cap_top=True)


def _frond(m: Mesh, root, yaw: float, height: float, reach: float, width: float, zone: int, leaflets: int = 10,
           tip_droop: float = 0.06) -> None:
    """A fern frond rising from the pot and curving outward (a vase of
    fronds), its tip nodding over: paired leaflets as double-sided cards
    pointing up and out along the frond."""
    def at(t):
        r = reach * t ** 1.6
        y = height * math.sin(math.pi * 0.5 * t) - tip_droop * max(0.0, t - 0.7) / 0.3
        return (root[0] + r * math.sin(yaw), root[1] + y, root[2] + r * math.cos(yaw))

    side = (math.cos(yaw), 0.0, -math.sin(yaw))
    spine = [at(i / leaflets) for i in range(leaflets + 1)]
    for i in range(leaflets):
        a, b = spine[i], spine[i + 1]
        t = (i + 0.5) / leaflets
        lw = width * math.sin(math.pi * (0.15 + 0.85 * t)) ** 0.7
        along = mk.normalize(mk.sub(b, a))
        for s in (1, -1):
            tip = mk.add(mk.add(mk.lerp(a, b, 0.6), mk.scale(side, lw * s)), mk.scale(along, lw * 0.7))
            pts = [a, b, tip] if s > 0 else [b, a, tip]
            _double_sided(m, pts, "leaf", zone, [(0.5, 0.0), (0.5, 1.0), (1.0 if s > 0 else 0.0, 0.5)])


def fern_potted(item: dict) -> Mesh:
    """A Boston fern in a clay pot: a vase of fronds curving out and over."""
    f = item["furniture"]
    w = f["footprint"][0]
    h = f["height"]
    m = Mesh()
    pot_h = 0.3
    _clay_pot(m, 0.15, 0.11, pot_h, SECONDARY)
    rng = _Rand(5)
    n = 18
    mark = m.mark()
    for k in range(n):
        yaw = 2 * math.pi * k / n + rng.uniform(-0.15, 0.15)
        inner = k % 3 == 0
        height = (h - pot_h - 0.02) * (rng.uniform(0.85, 1.0) if inner else rng.uniform(0.55, 0.8))
        reach = rng.uniform(0.1, 0.14) if inner else rng.uniform(0.2, 0.26)
        _frond(m, (0.02 * math.sin(yaw), pot_h - 0.01, 0.02 * math.cos(yaw)), yaw, height, reach, 0.032,
               PRIMARY if k % 5 else TRIM, tip_droop=0.04 if inner else 0.1)
    _fit_spread(m, mark, w, f["footprint"][1])
    return m


def herb_pot(item: dict) -> Mesh:
    """A windowsill pot of herbs: bushy sprigs crowded with small leaves."""
    f = item["furniture"]
    w = f["footprint"][0]
    h = f["height"]
    m = Mesh()
    pot_h = 0.12
    _clay_pot(m, w / 2 - 0.01, w / 2 - 0.035, pot_h, SECONDARY)
    rng = _Rand(9)
    mark = m.mark()
    for k in range(9):
        a = 2 * math.pi * k / 9 + rng.uniform(-0.3, 0.3)
        rr = 0.03 if k else 0.0
        x0, z0 = rr * math.sin(a), rr * math.cos(a)
        top_y = h - rng.uniform(0.0, 0.06)
        lean = rng.uniform(0.01, 0.03) if k else 0.0
        x1, z1 = x0 + lean * math.sin(a), z0 + lean * math.cos(a)
        mk.rod(m, (x0, pot_h - 0.015, z0), (x1, top_y, z1), 0.003, 3, "leaf", TRIM, caps=False)
        for j in range(5):
            t = 0.25 + 0.17 * j
            p = (x0 + (x1 - x0) * t, pot_h + (top_y - pot_h) * t, z0 + (z1 - z0) * t)
            yaw = a + j * 1.9
            size = 0.042 * (1.1 - 0.4 * t)
            for s in (1, -1):
                d = (math.sin(yaw) * s, 0.0, math.cos(yaw) * s)
                tip = (p[0] + d[0] * size, p[1] + size * 0.45, p[2] + d[2] * size)
                sd = (math.cos(yaw) * size * 0.4, 0.0, -math.sin(yaw) * size * 0.4)
                quad = [p, mk.add(mk.lerp(p, tip, 0.5), sd), tip, mk.sub(mk.lerp(p, tip, 0.5), sd)]
                _double_sided(m, quad, "leaf", PRIMARY if (j + k) % 4 else TRIM,
                              [(0.5, 0.0), (1.0, 0.5), (0.5, 1.0), (0.0, 0.5)])
    _fit_spread(m, mark, w, f["footprint"][1], grow=False)
    return m


def _fit_spread(m: Mesh, mark, w: float, d: float, grow: bool = True) -> None:
    """Stretches (or squeezes) the parts added since `mark` in x and z, each
    side of each axis on its own (a continuous warp), so foliage spans the
    footprint exactly; with `grow` False it only ever squeezes."""
    pts = m.verts[mark[0]:]
    lim = {(k, sgn): max(max(p[k] * sgn for p in pts), 1e-3) for k in (0, 2) for sgn in (1, -1)}
    half = {0: w / 2 - 0.005, 2: d / 2 - 0.005}

    def fit(p):
        q = list(p)
        for k in (0, 2):
            s = half[k] / lim[(k, 1 if p[k] >= 0 else -1)]
            q[k] = p[k] * (s if grow else min(1.0, s))
        return tuple(q)

    m.transform(mark, fit)


def candelabra_brass(item: dict) -> Mesh:
    """A three-branch brass candelabra: a domed foot, a knopped stem, two
    curving arms and three lit candles."""
    f = item["furniture"]
    w = f["footprint"][0]
    h = f["height"]
    m = Mesh()
    r = w / 2
    mk.lathe(m, [(r, 0.0), (r, 0.01), (r * 0.8, 0.025), (r * 0.35, 0.05), (0.025, 0.07)], 12, "metal", PRIMARY,
             cap_top=False, cap_bottom=True)
    stem_top = h - 0.17
    mk.lathe(m, [(0.025, 0.07), (0.014, 0.1), (0.03, 0.13), (0.014, 0.16), (0.014, stem_top - 0.03), (0.026, stem_top)],
             8, "metal", PRIMARY, cap_top=False)
    cups = [(0.0, stem_top + 0.04)]
    for sx in (-1, 1):
        pts = []
        for i in range(7):
            t = i / 6
            pts.append((sx * (r - 0.03) * t, stem_top - 0.02 + 0.05 * math.sin(math.pi * t * 0.5) - 0.03 * math.sin(math.pi * t),
                        0.0))
        for p0, p1 in zip(pts, pts[1:]):
            mk.rod(m, p0, p1, 0.008, 5, "metal", PRIMARY, caps=False)
        cups.append((sx * (r - 0.03), pts[-1][1] + 0.01))
    mk.lathe(m, [(0.012, stem_top - 0.01), (0.012, stem_top + 0.04)], 6, "metal", PRIMARY, cap_top=False)
    for cx, cy in cups:
        mk.lathe(m, [(0.008, 0.0), (0.03, 0.015), (0.024, 0.03)], 8, "metal", TRIM, (cx, cy - 0.01, 0.0), cap_top=False)
        mk.cylinder(m, (cx, cy + 0.01, 0.0), 0.011, 0.075, 6, "wax", UNDYED)
        FI.note(m, candles=[(cx, cy + 0.085, 0.0, 0.045)])  # the flame: the client's animated fire
    return m


def table_round_side(item: dict) -> Mesh:
    """A round side table: a moulded top on a turned pedestal with three
    curving feet capped in brass."""
    f = item["furniture"]
    w = f["footprint"][0]
    top = f["top"]
    m = Mesh()
    r = w / 2
    mk.lathe(m, [(r - 0.02, top - 0.035), (r, top - 0.022), (r, top - 0.008), (r - 0.006, top)], 18, "wood", PRIMARY,
             cap_top=True, cap_bottom=True)
    mk.lathe(m, [(0.06, 0.12), (0.04, 0.16), (0.055, 0.22), (0.03, 0.3), (0.03, top - 0.12), (0.05, top - 0.07),
                 (0.07, top - 0.035)], 10, "wood", PRIMARY, cap_top=False, cap_bottom=True)
    for k in range(3):
        a = 2 * math.pi * k / 3
        pts = []
        for i in range(6):
            t = i / 5
            rr = 0.04 + (r * 0.82 - 0.04) * t
            y = 0.15 * (1 - t) ** 1.6 + 0.02
            pts.append((rr * math.sin(a), y, rr * math.cos(a)))
        for p0, p1 in zip(pts, pts[1:]):
            mk.rod(m, p0, p1, 0.02, 5, "wood", PRIMARY, caps=False)
        foot = pts[-1]
        mk.lathe(m, [(0.022, 0.0), (0.026, 0.01), (0.02, 0.025)], 6, "metal", TRIM, (foot[0], 0.0, foot[2]))
    return m


MODELS = {
    "furniture/bed_four_poster_a": bed_four_poster,
    "furniture/trunk_school_a": trunk_school,
    "furniture/table_bedside_a": table_bedside,
    "furniture/desk_writing_a": desk_writing,
    "furniture/chair_wooden_a": chair_wooden,
    "furniture/armchair_velvet_a": armchair_velvet,
    "furniture/rug_round_a": rug_round,
    "furniture/bookcase_oak_a": bookcase_oak,
    "furniture/banner_house_a": banner_house,
    "furniture/banner_house_b": banner_house,
    "furniture/banner_house_c": banner_house,
    "furniture/banner_house_d": banner_house,
    "furniture/painting_landscape_a": painting_landscape,
    "furniture/cage_owl_a": cage_owl,
    "furniture/lantern_candle_a": lantern_candle,
    "furniture/cauldron_pewter_a": cauldron_pewter,
    "furniture/stool_three_leg_a": stool_three_leg,
    "furniture/fern_potted_a": fern_potted,
    "furniture/herb_pot_a": herb_pot,
    "furniture/candelabra_brass_a": candelabra_brass,
    "furniture/table_round_side_a": table_round_side,
    # A simple scripted pennant.
    "furniture/pennant_house_cup_a": pennant_house_cup,
}

# Lights that come with an item (local coordinates), written to the manifest.
ITEM_LIGHTS = {
    "furniture/lantern_candle_a": [{"pos": [0.0, 0.2, 0.0], "color": "#FFC46E", "intensity": 2.0, "range": 4.0}],
    # The brew's green glow and the fire under the cauldron.
    "furniture/cauldron_pewter_a": [{"pos": [0.0, 0.62, 0.0], "color": "#A4E276", "intensity": 1.4, "range": 3.0},
                                    {"pos": [0.0, 0.15, 0.0], "color": "#FF9440", "intensity": 1.2, "range": 2.5}],
    "furniture/candelabra_brass_a": [{"pos": [0.0, 0.4, 0.0], "color": "#FFC46E", "intensity": 2.4, "range": 4.5}],
}


def build(item: dict) -> Mesh:
    """The model for a catalog furniture entry (by its asset key)."""
    try:
        fn = MODELS[item["asset"]]
    except KeyError:
        raise KeyError(f"no model for furniture asset {item['asset']!r} (item {item['id']})") from None
    return fn(item)
