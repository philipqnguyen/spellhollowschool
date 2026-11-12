"""The Great Hall's furniture (castle kit M3, docs/art/castle-kit.md):
the long house tables and their benches, the staff table, the staff
chairs and the high chair, the gilt owl lectern, the hourglass set, the
table settings and the floating candles. Human scale: tables
0.76 m high and 1 m deep, benches 0.45 m, as the greybox props were.

Frames as props.json `kit`: origin at the bottom centre (tables, benches,
chairs: long along local x; the chair row: its centre; the hourglass set:
its back's middle at the wall; table settings: the table top's centre),
yaw 0 facing +z. Players walk through all of it (`walkthrough`). The
candles are a group built in world coordinates (origin 0, yaw 0) from a
castle_kit.json `candles` entry; its LOD1 faces are a glowing point per
candle.

Colours come only from palette data: oak from the wood sheet
(castle_wood), the metals, glass, gems, cushions and food from props-atlas
names (`gilt`, `brass`, `pewter`, `hourglass_glass`, `house_a`..`house_d`,
`wine_red`, `ink_blue`, `food_*`), the candles' wax tile and `glow_candle`.

Pieces carry `meta["seats"]`: [x0, z0, x1, z1, y, yaw, front, back, drop]
in their own frame (a sitter facing `yaw`, 0 = +z; `front` / `back` how far
the seat reaches ahead of / behind the row's centre line, its front edge and
its backrest's face or back edge; `drop` the top above the floor the feet
rest on: client/src/game/characters/seated.ts fits the sitter's legs to
them), which assemble.py turns into the manifest's `seats`."""

from __future__ import annotations

import math
import random

import meshkit as mk
from castle_kit import grid as G
from castle_kit.pieces import geom, hall
from castle_kit.pieces.geom import Piece, add_face

BEAM = "castle_wood|trim:beam"
PLANKS = "castle_wood|trim:planks"
PANEL = "castle_wood|trim:wainscot"
TABLE_TOP = 0.76  # the house tables' top (the greybox props' height)
TABLE_DEPTH = 1.0
BENCH_SEAT = 0.45
BENCH_DEPTH = 0.4
LEG_PITCH = 2.4  # trestles every this along a long table (and legs along a bench)

GILT, BRASS, PEWTER, GLASS, CUSHION, ORB, BREAD, FRUIT, GREEN = range(1, 10)
HOUSE = {"a": 11, "b": 12, "c": 13, "d": 14}
ZONES = {GILT: "gilt", BRASS: "brass", PEWTER: "pewter", GLASS: "hourglass_glass", CUSHION: "wine_red", ORB: "ink_blue",
         BREAD: "food_bread", FRUIT: "food_fruit", GREEN: "food_green", 11: "house_a", 12: "house_b", 13: "house_c",
         14: "house_d"}


def _wood_uv(axis: int, lo, hi):
    """Strip UVs for a wooden box long along `axis` (0 = x, 2 = z): s along
    it, p across the face (scaled into the 0.6 m beam band)."""

    def uv(side, pts):
        out = []
        for pt in pts:
            s = pt[axis]
            if side in ("+y", "-y"):
                c, a, b = pt[2 - axis], lo[2 - axis], hi[2 - axis]
            elif side in ("+x", "-x") and axis == 0 or side in ("+z", "-z") and axis == 2:
                c, a, b = pt[1], lo[1], hi[1]
                s = pt[2 - axis]
            else:
                c, a, b = pt[1], lo[1], hi[1]
            span = (b - a) or 1.0
            out.append((s, (c - a) / span * min(0.6, span)))
        return out

    return uv


def wbox(f, lo, hi, tag=BEAM, skip=(), axis: int = 0):
    """An oak box (castle_wood strip UVs, long along `axis`)."""
    geom.box(f, lo, hi, tag, skip=skip, uv_fn=_wood_uv(axis, lo, hi))


# -- tables and benches ---------------------------------------------------------------------


def _legs_along(length: float, pitch: float = LEG_PITCH, inset: float = 0.35) -> list[float]:
    """Positions of trestles along a length: both ends `inset` in, evenly between."""
    n = max(1, round((length - 2 * inset) / pitch))
    return [-length / 2 + inset + (length - 2 * inset) * i / n for i in range(n + 1)]


def table_house(length: float, depth: float = TABLE_DEPTH, height: float = TABLE_TOP) -> Piece:
    """`kit_table_house_<len>`: a long oak refectory table on trestles
    (splayed legs on a foot, a rail under the top, a stretcher along the
    floor); at most 600 triangles per 12 m."""
    p = Piece("", walkthrough=True)
    f = p.faces
    hl, hd = length / 2.0, depth / 2.0
    top0 = height - 0.07
    wbox(f, (-hl, top0, -hd), (hl, height, hd))
    # Aprons under the top's long edges.
    for sz in (-1.0, 1.0):
        z0, z1 = sorted((sz * (hd - 0.08), sz * (hd - 0.12)))
        wbox(f, (-hl + 0.2, top0 - 0.12, z0), (hl - 0.2, top0, z1), skip=("+y", "+x", "-x"))
    for x in _legs_along(length):
        # A trestle: two legs, a foot and a cross rail.
        for sz in (-1.0, 1.0):
            z0, z1 = sorted((sz * 0.32, sz * 0.24))
            wbox(f, (x - 0.06, 0.08, z0), (x + 0.06, top0 - 0.12, z1), skip=("+y", "-y"), axis=0)
        wbox(f, (x - 0.08, 0.0, -0.4), (x + 0.08, 0.08, 0.4), skip=("-y",), axis=2)
        wbox(f, (x - 0.045, top0 - 0.24, -0.24), (x + 0.045, top0 - 0.12, 0.24), skip=("+y", "+z", "-z"), axis=2)
    wbox(f, (-hl + 0.35, 0.18, -0.05), (hl - 0.35, 0.28, 0.05), skip=("+x", "-x"))
    p.meta.update(length=length, height=height, depth=depth)
    return p


def bench(length: float, height: float = BENCH_SEAT, depth: float = BENCH_DEPTH) -> Piece:
    """`kit_bench_<len>`: a long oak bench, its seat `height` up on plain
    legs; at most 300 triangles per 12 m. Its seat row faces +z (yaw 0
    north of a table, pi south: the benches face their table)."""
    p = Piece("", walkthrough=True)
    f = p.faces
    hl, hd = length / 2.0, depth / 2.0
    wbox(f, (-hl, height - 0.06, -hd), (hl, height, hd))
    for x in _legs_along(length, LEG_PITCH, 0.3):
        wbox(f, (x - 0.05, 0.0, -hd + 0.05), (x + 0.05, height - 0.06, hd - 0.05), skip=("+y", "-y"), axis=2)
    wbox(f, (-hl + 0.3, 0.12, -0.035), (hl - 0.3, 0.2, 0.035), skip=("+x", "-x"))
    p.meta.update(length=length, height=height, seats=[[-hl, -hd, hl, hd, height, 0.0, hd, hd, height]])
    return p


STAFF_BOARD = 0.04  # the staff table's front board: its back this far behind its face


def table_staff(length: float, depth: float = TABLE_DEPTH, height: float = TABLE_TOP) -> Piece:
    """`kit_table_staff_<len>`: the staff table on the dais, long along
    local x, its panelled front (the wainscot strip's fielded panels) facing
    the hall (+z), legs and a rail behind; at most 600 triangles per 12 m
    (800 at 16 m)."""
    p = Piece("", walkthrough=True)
    f = p.faces
    hl, hd = length / 2.0, depth / 2.0
    top0 = height - 0.08
    wbox(f, (-hl - 0.05, top0, -hd - 0.04), (hl + 0.05, height, hd + 0.04))
    # The front: a panelled board from the floor plinth to under the top.
    fz = hd - 0.02
    add_face(f, [(-hl, 0.1, fz), (hl, 0.1, fz), (hl, top0, fz), (-hl, top0, fz)], PANEL,
             [(-hl, 0.4), (hl, 0.4), (hl, 2.75), (-hl, 2.75)], (0.0, 0.0, 1.0), anchor=True)
    wbox(f, (-hl - 0.01, 0.0, fz - 0.04), (hl + 0.01, 0.1, fz + 0.05), skip=("-y", "-z"))
    # End boards, inner legs and a back rail, each a little off the others'
    # planes (no shared faces).
    for sx in (-1.0, 1.0):
        x0, x1 = sorted((sx * hl, sx * (hl - 0.12)))
        wbox(f, (x0, 0.0, -hd + 0.04), (x1, top0, fz - 0.01), skip=("-y", "+y"), axis=2)
    for x in _legs_along(length, 2.0, 0.6)[1:-1]:
        wbox(f, (x - 0.06, 0.0, -hd + 0.08), (x + 0.06, top0 - 0.14, -hd + 0.2), skip=("-y", "+y"))
    wbox(f, (-hl + 0.12, top0 - 0.14, -hd + 0.06), (hl - 0.12, top0, -hd + 0.13), skip=("+y", "+x", "-x"))
    # Portrait back: the front board's back, so from the dais behind the table it is a board
    # (it was drawn toward the hall only: seen from behind, the hall showed through it).
    wbox(f, (-hl + 0.12, 0.0, fz - STAFF_BOARD), (hl - 0.12, top0, fz - 0.01), skip=("+z", "+y", "-y", "+x", "-x"))
    p.meta.update(length=length, height=height, depth=depth)
    return p


# -- chairs ------------------------------------------------------------------------------------


CHAIR_FRONT = 0.24  # a chair's seat board and front legs reach this far ahead of its row's centre line
CHAIR_BACK = 0.19  # its back panel's face this far behind


def _chair(f, m, x: float, width: float = 0.56, back_h: float = 1.35, seat: float = 0.46, arms: bool = False,
           gable: float = 0.18, finials: bool = True) -> None:
    """One chair centred on local x, facing +z: oak seat, legs and a back
    with a pointed head, a cushion (props `wine_red`) and gilt finials."""
    hw = width / 2.0
    d0, d1 = -CHAIR_FRONT, CHAIR_FRONT
    wbox(f, (x - hw, seat - 0.05, d0), (x + hw, seat, d1))
    for sx in (-1.0, 1.0):
        lx = x + sx * (hw - 0.04)
        wbox(f, (lx - 0.035, 0.0, d1 - 0.07), (lx + 0.035, seat - 0.05, d1), skip=("+y", "-y"))
        # The back stiles run from the floor to the back's head (a little
        # wider and deeper than the seat, so no face lies in the seat's planes).
        wbox(f, (lx - 0.045, 0.0, d0 - 0.01), (lx + 0.045, back_h, d0 + 0.07), skip=("-y",))
        if arms:
            wbox(f, (lx - 0.035, seat + 0.24, d0 + 0.07), (lx + 0.035, seat + 0.29, d1), skip=("-z",), axis=2)
            wbox(f, (lx - 0.03, seat, d1 - 0.07), (lx + 0.03, seat + 0.24, d1 - 0.01), skip=("+y", "-y"))
        if finials:
            mk.lathe(m, [(0.0, 0.0), (0.045, 0.03), (0.035, 0.09), (0.0, 0.16)], 4, "metal", GILT,
                     (lx, back_h, d0 + 0.035), phase=math.pi / 4, cap_top=False)
    # The back: a panel between the stiles with a pointed (gabled) head.
    x0, x1 = x - hw + 0.08, x + hw - 0.08
    yb, yh = seat + 0.08, back_h - 0.02
    outline = [(x0, yb), (x1, yb), (x1, yh - gable), (x, yh), (x0, yh - gable)]
    zf, zb = -CHAIR_BACK, d0 + 0.02
    add_face(f, [(px, py, zf) for px, py in outline], PANEL, [(px, 1.25 + (py - yb) * 1.1) for px, py in outline],
             (0.0, 0.0, 1.0), anchor=True)
    add_face(f, [(px, py, zb) for px, py in reversed(outline)], PANEL,
             [(px, 1.25 + (py - yb) * 1.1) for px, py in reversed(outline)], (0.0, 0.0, -1.0), anchor=True)
    for (ax, ay), (bx, by) in ((outline[2], outline[3]), (outline[3], outline[4])):
        add_face(f, [(ax, ay, zb), (bx, by, zb), (bx, by, zf), (ax, ay, zf)], BEAM,
                 [(ax, 0.0), (bx, 0.0), (bx, 0.03), (ax, 0.03)], (by - ay, -(bx - ax), 0.0))
    # Cushion.
    mk.box(m, (x - hw + 0.04, seat, d0 + 0.09), (x + hw - 0.04, seat + 0.06, d1 - 0.03), "leather", CUSHION, skip=("-y",))


def chair_row_staff(count: int = 18, pitch: float = 0.8, gap: float = 1.4) -> Piece:
    """`kit_chair_row_staff`: `count` staff chairs along local x, `pitch`
    apart, half either side of a central `gap` (where the high chair
    stands), facing +z; about 120 triangles a chair (budget 300)."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    n = int(round(count))
    per = n // 2
    xs = sorted([-(gap / 2 + pitch / 2 + i * pitch) for i in range(per)] +
                [gap / 2 + pitch / 2 + i * pitch for i in range(n - per)])
    seats = []
    for x in xs:
        _chair(p.faces, m, x, width=min(0.62, pitch - 0.14))
        seats.append([round(x - 0.28, 4), -0.24, round(x + 0.28, 4), 0.24, 0.52, 0.0, CHAIR_FRONT, CHAIR_BACK, 0.52])
    p.faces.extend(hall.from_mesh(m, ZONES).faces)
    p.meta.update(count=n, pitch=pitch, gap=gap, chairs=xs, seats=seats)
    return p


def chair_high() -> Piece:
    """`kit_chair_high`: the high chair at the middle of the staff table:
    wider, arms, a tall back with a gabled head and a gilt finial; at most
    600 triangles."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    _chair(p.faces, m, 0.0, width=0.86, back_h=2.4, seat=0.5, arms=True, gable=0.42)
    mk.lathe(m, [(0.0, 0.0), (0.07, 0.05), (0.05, 0.16), (0.09, 0.24), (0.0, 0.42)], 6, "metal", GILT, (0.0, 2.38, -0.205),
             cap_top=False)
    p.faces.extend(hall.from_mesh(m, ZONES).faces)
    p.meta.update(seats=[[-0.38, -0.24, 0.38, 0.24, 0.56, 0.0, CHAIR_FRONT, CHAIR_BACK, 0.56]])
    return p


# -- the owl lectern -----------------------------------------------------------------------------


def owl_lectern(height: float = 1.32) -> Piece:
    """`kit_owl_lectern`: a gilt lectern (our own design): an octagonal
    stepped foot, a column ringed with mouldings and a knop holding a dark
    blue orb, on which an owl stands with its wings raised and spread to
    carry the sloped reading desk; the owl faces +z (the hall), the reader
    stands behind it (-z). At most 2,500 triangles."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    oct8 = math.pi / 8
    mk.lathe(m, [(0.42, 0.0), (0.42, 0.08), (0.34, 0.1), (0.34, 0.16), (0.26, 0.2), (0.24, 0.26)], 8, "metal", GILT,
             phase=oct8)
    mk.lathe(m, [(0.1, 0.26), (0.085, 0.4), (0.12, 0.44), (0.12, 0.48), (0.08, 0.52), (0.07, 0.72), (0.11, 0.76),
                 (0.11, 0.8), (0.075, 0.84)], 8, "metal", GILT, phase=oct8, cap_top=False)
    # The knop: a cup holding the orb.
    mk.lathe(m, [(0.075, 0.84), (0.16, 0.9), (0.17, 0.94)], 8, "metal", GILT, cap_top=False)
    orb = [(0.15 * math.sin(math.pi * i / 6), 0.15 - 0.15 * math.cos(math.pi * i / 6)) for i in range(7)]
    mk.lathe(m, [(r, y + 0.88) for r, y in orb], 8, "glass", ORB)
    # The owl standing on the orb: body, head with a facial disc and ear tufts, eyes.
    y0 = 1.16
    mk.lathe(m, [(0.0, 0.0), (0.13, 0.04), (0.17, 0.14), (0.16, 0.26), (0.12, 0.34), (0.0, 0.38)], 10, "metal", GILT,
             (0.0, y0 - 0.02, 0.0), scale_xz=(1.0, 0.85))
    mk.lathe(m, [(0.0, 0.0), (0.12, 0.03), (0.13, 0.11), (0.09, 0.17), (0.0, 0.19)], 10, "metal", GILT,
             (0.0, y0 + 0.33, 0.01))
    disc = [(0.085 * math.sin(2 * math.pi * i / 10), y0 + 0.43 + 0.075 * math.cos(2 * math.pi * i / 10), 0.125)
            for i in range(10)]
    m.poly(disc if mk.newell(disc)[2] > 0 else list(reversed(disc)), "metal", zone=BRASS)
    for sx in (-1.0, 1.0):
        mk.centred_box(m, (sx * 0.035, y0 + 0.44, 0.13), (0.035, 0.035, 0.012), "glass", ORB)
        tuft = [(sx * 0.06, y0 + 0.5, 0.06), (sx * 0.11, y0 + 0.6, 0.02), (sx * 0.1, y0 + 0.5, 0.0)]
        m.poly(tuft, "metal", zone=GILT)
        m.poly(list(reversed(tuft)), "metal", zone=GILT)
        # Talons gripping the orb.
        mk.lathe(m, [(0.03, 0.0), (0.0, 0.06)], 4, "metal", GILT, (sx * 0.06, y0 - 0.04, 0.07))
        # A raised, spread wing: three tiers of feathers, a slab each, rising
        # outward and back from the shoulder to carry the desk's edge.
        for tier, (reach, lift, depth) in enumerate(((0.62, 0.36, 0.1), (0.52, 0.24, 0.07), (0.38, 0.12, 0.05))):
            yb = y0 + 0.18 + tier * 0.02
            sh = (sx * 0.12, yb, 0.0)
            tip = (sx * (0.12 + reach), yb + lift + 0.1, -0.06 - tier * 0.04)
            n = 4
            for k in range(n):
                a0, a1 = k / n, (k + 1) / n
                p0 = (sh[0] + (tip[0] - sh[0]) * a0, sh[1] + (tip[1] - sh[1]) * a0 - 0.12 * a0 * (1 - a0),
                      sh[2] + (tip[2] - sh[2]) * a0)
                p1 = (sh[0] + (tip[0] - sh[0]) * a1, sh[1] + (tip[1] - sh[1]) * a1 - 0.12 * a1 * (1 - a1),
                      sh[2] + (tip[2] - sh[2]) * a1)
                hgt = 0.2 - 0.03 * tier - 0.12 * a1
                q = [(p0[0], p0[1] - (0.2 - 0.03 * tier - 0.12 * a0), p0[2]), (p1[0], p1[1] - hgt, p1[2]), p1, p0]
                if sx < 0:
                    q = list(reversed(q))
                front = [(x, y, z + depth / 2) for x, y, z in q]
                back = [(x, y, z - depth / 2) for x, y, z in reversed(q)]
                m.poly(front, "metal", zone=GILT)
                m.poly(back, "metal", zone=GILT)
                top = [(p0[0], p0[1], p0[2] - depth / 2), (p0[0], p0[1], p0[2] + depth / 2), (p1[0], p1[1], p1[2] + depth / 2),
                       (p1[0], p1[1], p1[2] - depth / 2)]
                if sx > 0:
                    top = list(reversed(top))
                m.poly(top, "metal", zone=GILT)
    # The reading desk on the wings' tips: a sloped board facing the reader (-z) with a book ledge.
    dy, dz = height, -0.12
    w, d = 0.86, 0.5
    slope = math.radians(20)
    c, s = math.cos(slope), math.sin(slope)

    def desk(px, py, pz):  # rotate about x so the board falls toward -z
        return (px, dy + py * c + pz * s, dz + (-py * s + pz * c))

    pts = [desk(x, y, z) for x, y, z in ((-w / 2, 0.0, -d / 2), (w / 2, 0.0, -d / 2), (w / 2, 0.0, d / 2), (-w / 2, 0.0, d / 2))]
    top = [desk(x, 0.04, z) for x, _, z in ((-w / 2, 0, -d / 2), (w / 2, 0, -d / 2), (w / 2, 0, d / 2), (-w / 2, 0, d / 2))]
    m.poly(list(reversed(top)) if mk.newell(top)[1] < 0 else top, "metal", zone=GILT)
    m.poly(pts if mk.newell(pts)[1] < 0 else list(reversed(pts)), "metal", zone=GILT)
    for i in range(4):
        a, b = pts[i], pts[(i + 1) % 4]
        q = [a, b, top[(i + 1) % 4], top[i]]
        m.poly(q, "metal", zone=GILT)
    ledge = [desk(x, y, z) for x, y, z in ((-w / 2, 0.04, -d / 2 - 0.0), (w / 2, 0.04, -d / 2), (w / 2, 0.1, -d / 2),
                                            (-w / 2, 0.1, -d / 2))]
    m.poly(ledge if mk.newell(ledge)[2] < 0 else list(reversed(ledge)), "metal", zone=BRASS)
    p.faces.extend(hall.from_mesh(m, ZONES, walkthrough=True).faces)
    p.meta.update(height=height + 0.2)
    return p


# -- the hourglass set -------------------------------------------------------------------------

HOURGLASS_R = 0.36
HOURGLASS_BACK = 0.32  # the frame's back stands this far off the wall (clear of the shafts and the wainscot)
# The sand stands this far proud of the lower glass (no z-fight at range).
SAND_PROUD = 0.012


def hourglass_set(count: int = 4, pitch: float = 2.5, height: float = 4.5) -> Piece:
    """`kit_hourglass_set`: `count` tall hourglasses `pitch` apart in a gilt
    frame on a stone plinth against a wall (the house points): each glass a
    tall lower cylinder of pale glass with the sand (gems) over it in its
    own group (`hourglass_sand_house_<x>`, modelled full, scaled
    from its base by the client with the House Cup standings; everything is
    opaque), a neck and an upper bulb, gilt caps; gilt posts with knops
    between the glasses, top and bottom rails. Sand colours are the palette
    names `house_a`..`house_d` in that order. At most 3,000 triangles.
    meta["sand"]: group -> [(x, y0, z, y1)] of its glasses (the sand's
    axis and its bottom and top heights, in the piece's frame)."""
    p = Piece("", walkthrough=True)
    f = p.faces
    m = mk.Mesh()
    sand: dict = {}
    n = int(round(count))
    xs = [(i - (n - 1) / 2.0) * pitch for i in range(n)]
    zc = HOURGLASS_BACK + 0.04 + HOURGLASS_R
    half = xs[-1] + pitch / 2.0 - 0.3
    # The stone plinth (castle_trim).
    z0, z1 = HOURGLASS_BACK, zc + HOURGLASS_R + 0.3
    geom.box(f, (-half, 0.0, z0), (half, 0.55, z1), "castle_trim|trim:plinth", skip=("-y", "-z"),
             uv_fn=lambda side, pts: [(pt[0] + pt[2], min(0.6, pt[1] / 0.55 * 0.6)) for pt in pts])
    yb = 0.55
    top = height
    rail = 0.12
    for i, x in enumerate(xs):
        house = "abcd"[i % 4]
        zone = HOUSE[house]
        r = HOURGLASS_R
        y0, y1 = yb + 0.22, top - 1.25  # the lower glass
        # Gilt foot and cap of the lower glass.
        mk.lathe(m, [(r + 0.08, yb), (r + 0.08, yb + 0.1), (r + 0.02, yb + 0.16), (r + 0.02, y0)], 10, "metal", GILT,
                 (x, 0.0, zc), cap_top=False)
        # The lower glass is pale glass all the way up; the sand is its own
        # moving node (grid.SAND_GROUP; glass i is house "abcd"[i % 4]), modelled full and a
        # little proud of the glass, which the client scales from its base (0 empty .. 1
        # full) with the week's standings.
        mk.lathe(m, [(r, y0), (r, y1), (r * 0.6, y1 + 0.22), (0.07, y1 + 0.45)], 10, "glass", GLASS, (x, 0.0, zc),
                 cap_top=False)
        ms = mk.Mesh()
        mk.lathe(ms, [(r + SAND_PROUD, y0), (r + SAND_PROUD, y1)], 10, "glass", zone, (x, 0.0, zc), cap_top=True)
        group = G.SAND_GROUP.format(house)
        for sf in hall.from_mesh(ms, ZONES).faces:
            sf.group = group
            f.append(sf)
        sand.setdefault(group, []).append((x, y0, zc, y1))
        # The upper bulb: a little sand left in its bottom.
        neck = y1 + 0.45
        mk.lathe(m, [(0.07, neck), (0.2, neck + 0.12)], 10, "glass", zone, (x, 0.0, zc), cap_top=False)
        mk.lathe(m, [(0.2, neck + 0.12), (r * 0.9, neck + 0.35), (r * 0.9, top - rail - 0.12), (r + 0.06, top - rail - 0.08)],
                 10, "glass", GLASS, (x, 0.0, zc), cap_top=False)
        mk.lathe(m, [(r + 0.06, top - rail - 0.08), (r + 0.08, top - rail), (0.0, top - rail + 0.02)], 10, "metal", GILT,
                 (x, 0.0, zc))
    # Posts between and beside the glasses, with knops and finials.
    posts = [xs[0] - pitch / 2.0 + 0.3] + [(a + b) / 2.0 for a, b in zip(xs, xs[1:])] + [xs[-1] + pitch / 2.0 - 0.3]
    for x in posts:
        mk.lathe(m, [(0.05, yb), (0.04, top)], 6, "metal", GILT, (x, 0.0, zc), cap_top=False)
        for ky in (yb + 0.9, top * 0.62):
            mk.lathe(m, [(0.04, 0.0), (0.08, 0.05), (0.04, 0.1)], 6, "metal", GILT, (x, ky, zc), cap_top=False)
        mk.lathe(m, [(0.0, 0.0), (0.09, 0.07), (0.07, 0.16), (0.0, 0.3)], 6, "metal", GILT, (x, top, zc), cap_top=False)
    # Rails along the top and over the plinth, behind the glasses.
    for ya, yb_ in ((top - rail, top), (yb + 0.02, yb + 0.12)):
        mk.box(m, (posts[0], ya, zc - 0.07), (posts[-1], yb_, zc - 0.02), "metal", GILT)
    p.faces.extend(hall.from_mesh(m, ZONES).faces)
    p.meta.update(count=n, pitch=pitch, height=height + 0.3, glasses=xs, wall=(-half, half, 0.0, height + 0.3),
                  sand=sand)
    return p


# -- table settings -----------------------------------------------------------------------------

SETTING_PITCH = 1.5  # one place this far apart along each side


def _plate(m, x, z, r=0.13):
    ring = [(x + r * math.cos(2 * math.pi * k / 8), 0.006, z + r * math.sin(2 * math.pi * k / 8)) for k in range(8)]
    m.poly(ring if mk.newell(ring)[1] > 0 else list(reversed(ring)), "metal", zone=PEWTER)


def _goblet(m, x, z):
    mk.lathe(m, [(0.04, 0.0), (0.012, 0.03), (0.045, 0.12), (0.045, 0.17)], 4, "metal", GILT, (x, 0.0, z),
             cap_top=True, cap_bottom=False, phase=math.pi / 4)


def _platter(m, x, z, kind: int):
    mk.lathe(m, [(0.0, 0.004), (0.24, 0.004), (0.26, 0.03)], 8, "metal", PEWTER, (x, 0.0, z), cap_top=False)
    if kind % 3 == 0:  # a loaf
        mk.lathe(m, [(0.14, 0.03), (0.15, 0.08), (0.08, 0.13), (0.0, 0.14)], 6, "fabric", BREAD, (x, 0.0, z),
                 scale_xz=(1.0, 0.6), cap_top=False)
    elif kind % 3 == 1:  # a pile of fruit
        for k in range(3):
            a = 2 * math.pi * k / 3
            r = 0.07
            mk.lathe(m, [(0.0, 0.0), (r, r * 0.6), (0.0, r * 1.6)], 4, "fabric", FRUIT,
                     (x + 0.08 * math.cos(a), 0.03, z + 0.08 * math.sin(a)), cap_top=False)
    else:  # greens in a bowl
        mk.lathe(m, [(0.06, 0.03), (0.16, 0.09), (0.0, 0.12)], 6, "fabric", GREEN, (x, 0.0, z), cap_top=False)


def _jug(m, x, z):
    mk.lathe(m, [(0.07, 0.0), (0.09, 0.08), (0.06, 0.22), (0.06, 0.27)], 5, "metal", PEWTER, (x, 0.0, z),
             cap_top=True)


STAFF_TABLE = 16.0  # kit_table_setting_16 lays the staff table: one side, at the staff chairs and the high chair


def staff_places(count: int = 18, pitch: float = 0.8, gap: float = 1.4) -> list[float]:
    """Places along the staff table: every chair of kit_chair_row_staff's
    default row and the high chair between them."""
    per = count // 2
    xs = [-(gap / 2 + pitch / 2 + i * pitch) for i in range(per)] + [gap / 2 + pitch / 2 + i * pitch
                                                                     for i in range(count - per)]
    return sorted(xs + [0.0])


def table_setting(length: float, sides: int | None = None, pitch: float = SETTING_PITCH, places=None) -> Piece:
    """`kit_table_setting_<len>`: pewter plates and gilt goblets at every
    place (`pitch` apart along both sides; the staff table's, length
    STAFF_TABLE, at its chairs along one side, -z, where the staff sit),
    platters of bread, fruit and greens and pewter jugs down the middle;
    origin on the table top's centre. At most 2,500 triangles per 43 m."""
    p = Piece("", walkthrough=True)
    m = mk.Mesh()
    hl = length / 2.0
    if sides is None:
        sides = 1 if abs(length - STAFF_TABLE) < 1e-6 else 2
    if places is None and sides == 1:
        places = staff_places()
    if places is None:
        n = max(1, int((length - 0.6) // pitch))
        xs = [-hl + (length - (n - 1) * pitch) / 2.0 + i * pitch for i in range(n)]
    else:
        xs = list(places)
    side_z = (-0.28, 0.28) if sides == 2 else (-0.26,)
    for x in xs:
        for z in side_z:
            _plate(m, x, z)
            _goblet(m, x + 0.2, z * 0.62)
    # Down the middle (or the front for one side): platters and jugs.
    mid_z = 0.0 if sides == 2 else 0.12
    step = 5.0
    k = max(1, int(length // step))
    for i in range(k):
        x = -hl + (i + 0.5) * length / k
        _platter(m, x, mid_z, i)
        _jug(m, x + 0.55, mid_z + (0.06 if sides == 2 else 0.0))
    p.faces.extend(hall.from_mesh(m, ZONES, walkthrough=True).faces)
    p.meta.update(length=length, places=len(xs) * len(side_z))
    return p


# -- floating candles ----------------------------------------------------------------------------

# Sized to read from the floor of a 66 m hall at 5-9 m up (review M3+M4: the 6 cm candles
# with 11 cm flames drew as one- or two-pixel dark dashes under the outline pass).
CANDLE_R = 0.05
CANDLE_WAX_H = (0.28, 0.06)  # height: base + seeded extra (with its flame at most grid.CANDLE_HEIGHT)
FLAME_H = 0.155
WAX = "castle_props|kit:wax"


def candle_positions(rect, y0: float, y1: float, count: int, seed: int) -> list[tuple[float, float, float, float]]:
    """(x, y, z, height) of a group's candles: stratified over the rect (a
    jittered grid, so they spread evenly without lining up), y and the
    candle's height (CANDLE_WAX_H: 0.28-0.34 m; with its flame within grid.CANDLE_HEIGHT)
    by seed."""
    rng = random.Random(seed)
    x0, z0, x1, z1 = rect
    w, d = x1 - x0, z1 - z0
    cols = max(1, round(math.sqrt(count * w / d)))
    rows = max(1, math.ceil(count / cols))
    cells = [(i, j) for j in range(rows) for i in range(cols)]
    rng.shuffle(cells)
    out = []
    for i, j in cells[:count]:
        x = x0 + (i + 0.15 + 0.7 * rng.random()) * w / cols
        z = z0 + (j + 0.15 + 0.7 * rng.random()) * d / rows
        y = y0 + (y1 - y0) * rng.random()
        h = CANDLE_WAX_H[0] + CANDLE_WAX_H[1] * rng.random()  # with its flame at most grid.CANDLE_HEIGHT
        out.append((round(x, 4), round(y, 4), round(z, 4), round(h, 4)))
    out.sort()
    return out


def candles_floating(rect=(0.0, 0.0, 4.0, 4.0), y0: float = 5.0, y1: float = 9.0, count: int = 20, seed: int = 1,
                     lod1: bool = True) -> Piece:
    """`kit_candles_floating`: a group of floating candles in world
    coordinates (origin 0, yaw 0): each a hexagonal wax candle (the props
    atlas's `wax` tile) whose flame stands on its top (meta "candles", the
    client's animated fire); 20 triangles a candle (budget 40). LOD1: the
    wax as a three-sided stick (6 triangles). The group's lowest point
    is its lowest candle's foot (the overhead rule: castle_kit.json keeps
    y0 - amp - 0.35 m at least 2.4 m over the floor)."""
    p = Piece("", walkthrough=True)
    f = p.faces
    lod: list = []
    flames: list = []
    for x, y, z, h in candle_positions(rect, y0, y1, int(count), int(seed)):
        ring0 = [(x + CANDLE_R * math.cos(2 * math.pi * k / 6), y, z + CANDLE_R * math.sin(2 * math.pi * k / 6))
                 for k in range(6)]
        ring1 = [(px, y + h, pz) for px, _, pz in ring0]
        for k in range(6):
            a, b = ring0[k], ring0[(k + 1) % 6]
            c, d = ring1[(k + 1) % 6], ring1[k]
            q = [a, b, c, d]
            mx, mz = (a[0] + b[0]) / 2 - x, (a[2] + b[2]) / 2 - z
            add_face(f, q, WAX, [(0.0, 0.0), (0.03, 0.0), (0.03, h), (0.0, h)], (mx, 0.0, mz))
        add_face(f, ring1, WAX, None, (0.0, 1.0, 0.0))
        add_face(f, ring0, WAX, None, (0.0, -1.0, 0.0))
        # The flame above the wick is the client's animated fire (meta "candles"; docs/architecture.md
        # "Rendering"): generous, so it reads from the floor, and drawn at every distance.
        fy = y + h + 0.005
        flames.append((x, round(fy, 4), z, FLAME_H))
        if lod1:
            # LOD1: the wax as a thin three-sided stick (the flame stays the client's).
            tri = [(x + CANDLE_R * math.cos(2 * math.pi * k / 3 + 0.5), z + CANDLE_R * math.sin(2 * math.pi * k / 3 + 0.5))
                   for k in range(3)]
            for k in range(3):
                (ax, az), (bx, bz) = tri[k], tri[(k + 1) % 3]
                q = [(ax, y, az), (bx, y, bz), (bx, y + h, bz), (ax, y + h, az)]
                add_face(lod, q, WAX, [(0.0, 0.0), (0.03, 0.0), (0.03, h), (0.0, h)], ((ax + bx) / 2 - x, 0.0, (az + bz) / 2 - z))
    p.lod1 = lod if lod1 else None
    p.meta.update(count=int(count), rect=list(rect), y=(y0, y1), lowest=min((pt[1] for fc in f for pt in fc.pts), default=y0),
                  candles=flames)
    return p
