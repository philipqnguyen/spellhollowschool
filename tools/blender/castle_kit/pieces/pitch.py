"""Pieces of the Flying Pitch (docs/architecture.md;
world/pitch_build.py places them in the resident `pitch` cell and
world/school_rooms_pitch.py round the Flying class's practice field,
`class_pitch`). Our own plain designs.

- `kit_pitch_hoop`: a goal hoop: a slender gilt pole on a turned foot and a
  ring `radius` in the local xy plane (it faces along z), its centre
  `height` over the ground. Origin at the pole's foot; the foot reaches 0.3 m
  below y 0. Art only (no collision: brooms pass through it).
- `kit_pitch_tower`: a spectator tower `size` square: a timber scaffold of
  four corner posts, ring beams and cross braces, a viewing box at the top
  (`height` - 3.4 m: a plank floor and a parapet banded in its house's
  colour), a slate pyramid roof whose eaves stand at `height` - 0.4 and whose
  apex rises at most TOWER_APEX over `height` (the layout's no-fly box ends
  1.5 m over the tower's solid), and two house drapes on every face (the
  castle's painted house banners, `castle_cloth` `banner_house_<x>`) hung
  under the box. `house` 0..3 picks house_a..house_d. Origin at the tower's
  centre on the ground.
- `kit_pitch_stand`: a covered stand: a plank deck `length` x `depth` at
  `deck` over the ground (the layout's walkable deck: its top is exactly
  y = `deck`), boarded down to the ground all round (walkers keep off the
  base: a solid in the layout), a front railing, roof posts and a slate roof
  sloping down to the field `roof` m over the deck at its front, a plank back
  wall, house-coloured hangings along the front, and a flight of `steps`
  treads at an end (`steps_at` +1: past x = `length`, -1: before x = 0,
  0: at both ends) rising to the deck. Frame: x along the stand from 0 to `length`, the front
  (the field side) at z = 0 and the back at z = -`depth`, y up from the
  ground. The benches are `kit_bench_<len>` pieces the builder places.
- `kit_pitch_gate`: the Pitch Gate: two oak posts, a lintel with a small
  slate cap and four house pennants (house_a..d) hanging from it. Origin on
  the ground at the gate's middle, the gate across x, its front +z.
- `kit_pitch_apron`: a flat ring of turf (`castle_floor` family, world UVs)
  round a rectangle, from `inner` to `outer` metres out, `drop` under y 0
  (class_pitch: the meadow beyond the field's low wall, seen from the air).

Colours: palette names only on existing props_slots rows (oak, oak_aged,
gilt, iron, letter_white, house_a..house_d on the fabric tile); the castle's
`castle_wood` planks and beams, `castle_roof` slates, `castle_cloth` house
banners (painted from palette house_a..d) and `castle_floor` turf. No props
slot is added and no colour literal is written here.
"""

from __future__ import annotations

import math

import meshkit as mk
from castle_kit import grid as G
from castle_kit.pieces import geom
from castle_kit.pieces.geom import Piece, add_face
from castle_kit.pieces.hall import from_mesh

PLANKS = "castle_wood|trim:planks"
BEAM = "castle_wood|trim:beam"
ROOF = "castle_roof|world"
TURF = "castle_floor|world"
SHINGLES = "castle_wood|trim:planks"  # a timber roof where castle_roof may not go (an instance room: no facade)
BOARD = 0.6  # castle_wood `planks` strip: 0.6 m of profile

OAK, OAK_AGED, GILT, IRON, WHITE, HOUSE_A, HOUSE_B, HOUSE_C, HOUSE_D = range(1, 10)
HOUSES = (HOUSE_A, HOUSE_B, HOUSE_C, HOUSE_D)
ZONES = {OAK: "oak", OAK_AGED: "oak_aged", GILT: "gilt", IRON: "iron", WHITE: "letter_white", HOUSE_A: "house_a",
         HOUSE_B: "house_b", HOUSE_C: "house_c", HOUSE_D: "house_d"}
TILE = {OAK: "wood", OAK_AGED: "wood", GILT: "metal", IRON: "metal", WHITE: "fabric", HOUSE_A: "fabric",
        HOUSE_B: "fabric", HOUSE_C: "fabric", HOUSE_D: "fabric"}
BANNERS = ("banner_house_a", "banner_house_b", "banner_house_c", "banner_house_d")

# Caps and sizes (tests/test_pitch.py checks them).
HOOP_HEIGHT = (3.0, 20.0)
HOOP_RADIUS = (0.8, 2.0)
HOOP_TRIS = 700
TOWER_SIZE = (3.0, 5.0)
TOWER_HEIGHT = (10.0, 24.0)
TOWER_APEX = 1.45  # the roof's apex (and its finial) at most this over `height`
TOWER_EAVES = 0.45  # the roof's overhang past the box (the layout's no-fly box is 0.5 m wider each side)
TOWER_TRIS = 3_000
STAND_TRIS_PER_M = 90
STAND_ROOF_FRONT = 0.45  # the roof's eave past the deck's front edge (the no-fly box: 0.5)
STAND_ROOF_RISE = 0.5  # the roof's back edge this over its front (`roof` is the front's height over the deck)
GATE_TRIS = 800


def _rand(seed: int, i: int) -> float:
    s = (int(seed) * 2654435761 + i * 40503 + 97) % 2**32
    s ^= s >> 13
    s = (s * 1274126177) % 2**32
    return (s >> 8) / float(1 << 24)


def _boards(f: list, corner, u, v, ulen: float, vlen: float, normal, s0: float = 0.0, tag: str = PLANKS) -> None:
    """A rectangle `ulen` along the unit vector `u` and `vlen` along `v`
    from `corner`, split into boards at most BOARD wide across `v`, the
    planks' grain along `u` (castle_wood `planks` strip UVs)."""
    n = max(1, math.ceil(vlen / BOARD - 1e-9))
    w = vlen / n
    for i in range(n):
        a = geom.add(corner, tuple(c * w * i for c in v))
        b = geom.add(a, tuple(c * ulen for c in u))
        c = geom.add(b, tuple(cc * w for cc in v))
        d = geom.add(a, tuple(cc * w for cc in v))
        off = s0 + 1.37 * i  # the boards' joints never line up
        add_face(f, [a, b, c, d], tag, [(off, 0.0), (off + ulen, 0.0), (off + ulen, w), (off, w)], normal)


def _cloth(f: list, pts, tile: str, normal) -> None:
    """A castle_cloth face (double-sided) fitted to a banner tile by its outline's bounds."""
    # Project on the face's plane: horizontal along the face, vertical up.
    n = geom.unit(geom.newell(pts))
    h = geom.unit((n[2], 0.0, -n[0])) if abs(n[1]) < 0.9 else (1.0, 0.0, 0.0)
    flat = [(geom.dot(p, h), p[1]) for p in pts]
    add_face(f, pts, f"castle_cloth|fit:{tile}", geom.bbox_uv(flat), normal)


def _roof_uv(tag: str, pts):
    """None for the world-mapped slates; for timber, the planks strip along the face's first edge."""
    if tag == ROOF:
        return None
    a, b = pts[0], pts[1]
    u = geom.unit(geom.sub(b, a))
    v = geom.cross(geom.unit(geom.newell(pts)), u)
    return [(geom.dot(geom.sub(p, a), u), min(0.6, abs(geom.dot(geom.sub(p, a), v)) * 0.12)) for p in pts]


# -- kit_pitch_hoop -----------------------------------------------------------------------------


def _torus(m: mk.Mesh, centre, radius: float, tube: float, seg: int, sides: int, zone: int) -> None:
    """A ring in the xy plane round `centre` (it faces along z)."""
    cx, cy, cz = centre
    g = m.group()
    for i in range(seg):
        a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
        for j in range(sides):
            b0, b1 = 2 * math.pi * j / sides, 2 * math.pi * (j + 1) / sides

            def p(a, b):
                r = radius + tube * math.cos(b)
                return (cx + r * math.cos(a), cy + r * math.sin(a), cz + tube * math.sin(b))

            m.poly([p(a0, b0), p(a1, b0), p(a1, b1), p(a0, b1)], "metal", zone=zone, group=g)


def hoop(height: float = 13.5, radius: float = 1.3, detail: float = 1) -> Piece:
    """`kit_pitch_hoop` (module doc); `detail` 0 is its LOD1 build."""
    if not HOOP_HEIGHT[0] - 1e-9 <= height <= HOOP_HEIGHT[1] + 1e-9:
        raise ValueError(f"kit_pitch_hoop: height must be within {HOOP_HEIGHT}")
    if not HOOP_RADIUS[0] - 1e-9 <= radius <= HOOP_RADIUS[1] + 1e-9:
        raise ValueError(f"kit_pitch_hoop: radius must be within {HOOP_RADIUS}")
    fine = detail >= 1
    m = mk.Mesh()
    seg = 8 if fine else 4
    # The turned foot, the pole, a collar under the ring.
    if fine:
        mk.lathe(m, [(0.32, -0.3), (0.3, 0.12), (0.18, 0.3), (0.11, 0.6)], seg, "metal", GILT, cap_top=False)
    pole_top = height - radius - 0.04
    mk.rod(m, (0.0, 0.6 if fine else -0.3, 0.0), (0.0, pole_top, 0.0), 0.075, seg, "metal", GILT, caps=False)
    if fine:
        for y in (pole_top * 0.33, pole_top * 0.66):
            mk.lathe(m, [(0.075, y - 0.08), (0.11, y), (0.075, y + 0.08)], seg, "metal", GILT, cap_top=False)
        mk.lathe(m, [(0.075, pole_top - 0.25), (0.13, pole_top - 0.08), (0.1, pole_top + 0.06)], seg, "metal", GILT,
                 cap_top=True)
    _torus(m, (0.0, height, 0.0), radius, 0.075 if fine else 0.09, 28 if fine else 14, 6 if fine else 4, GILT)
    p = from_mesh(m, ZONES, walkthrough=True)
    p.meta.update(height=height, radius=radius, centre=(0.0, height, 0.0))
    return p


def hoop_piece(height: float = 13.5, radius: float = 1.3) -> Piece:
    """The hoop with its LOD1 (a lighter ring and a plain pole)."""
    p = hoop(height, radius, 1)
    p.lod1 = hoop(height, radius, 0).faces
    return p


# -- kit_pitch_tower ----------------------------------------------------------------------------


def _tower(house: int, size: float, height: float, fine: bool, roof_tag: str = ROOF) -> Piece:
    p = Piece("", walkthrough=False)
    f = p.faces
    m = mk.Mesh()
    hs = size / 2.0
    post = 0.16
    box_floor = height - 3.4
    eaves = height - 0.4
    out = hs + 0.2  # the box's parapet stands out past the posts
    # Corner posts, ground to eaves.
    for sx in (-1, 1):
        for sz in (-1, 1):
            x, z = sx * (hs - post), sz * (hs - post)
            mk.box(m, (x - post, -0.3, z - post), (x + post, eaves, z + post), "wood", OAK_AGED, skip=("-y",))
    # Ring beams (every 4 m below the box) and, in the fine build, cross braces on each face.
    levels = [y for y in (4.0, 8.0, 12.0, 16.0, 20.0) if y < box_floor - 0.5]
    for y in levels:
        for k in range(4):
            a = k * math.pi / 2
            c, s = math.cos(a), math.sin(a)

            def rot(px, pz):
                return (px * c - pz * s, pz * c + px * s)

            (x0, z0), (x1, z1) = rot(-hs + 2 * post, hs - post), rot(hs - 2 * post, hs - post)
            mk.rod(m, (x0, y, z0), (x1, y, z1), 0.09, 4, "wood", OAK_AGED, caps=False)
    if fine:
        prev = 0.3
        for y in levels + [box_floor]:
            for k in range(4):
                a = k * math.pi / 2
                c, s = math.cos(a), math.sin(a)

                def rot(px, pz):
                    return (px * c - pz * s, pz * c + px * s)

                zf = hs - post
                for xa, xb in ((-hs + 2 * post, hs - 2 * post), (hs - 2 * post, -hs + 2 * post)):
                    (ax, az), (bx, bz) = rot(xa, zf), rot(xb, zf)
                    mk.rod(m, (ax, prev + 0.1, az), (bx, y - 0.1, bz), 0.06, 4, "wood", OAK, caps=False)
            prev = y
    # The box: a plank floor (top and underside), a parapet banded in the house colour.
    _boards(f, (-out, box_floor, out), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0), 2 * out, 2 * out, (0.0, 1.0, 0.0))
    add_face(f, [(-out, box_floor - 0.25, -out), (out, box_floor - 0.25, -out), (out, box_floor - 0.25, out),
                 (-out, box_floor - 0.25, out)], PLANKS, [(0, 0), (2 * out, 0), (2 * out, 0.6), (0, 0.6)],
             (0.0, -1.0, 0.0))
    par = 1.1
    zone = HOUSES[house]
    for k in range(4):
        a = k * math.pi / 2
        c, s = math.cos(a), math.sin(a)

        def w(px, py, pz):
            return (px * c - pz * s, py, pz * c + px * s)

        nrm = w(0.0, 0.0, 1.0)
        # Outside: the house band over a plank skirt; inside: planks.
        q = [w(-out, box_floor - 0.25, out), w(out, box_floor - 0.25, out), w(out, box_floor + 0.35, out),
             w(-out, box_floor + 0.35, out)]
        add_face(f, q, PLANKS, [(0, 0), (2 * out, 0), (2 * out, 0.6), (0, 0.6)], nrm)
        band = [w(-out, box_floor + 0.35, out), w(out, box_floor + 0.35, out), w(out, box_floor + par, out),
                w(-out, box_floor + par, out)]
        m.poly(band if mk.dot(mk.newell(band), nrm) > 0 else list(reversed(band)), "fabric", zone=zone)
        inner = [w(out - 0.08, box_floor, out - 0.08), w(-out + 0.08, box_floor, out - 0.08),
                 w(-out + 0.08, box_floor + par, out - 0.08), w(out - 0.08, box_floor + par, out - 0.08)]
        add_face(f, inner, PLANKS, [(0, 0), (2 * out, 0), (2 * out, 0.6), (0, 0.6)], (-nrm[0], -nrm[1], -nrm[2]))
        cap = [w(-out, box_floor + par, out), w(out, box_floor + par, out), w(out - 0.08, box_floor + par, out - 0.08),
               w(-out + 0.08, box_floor + par, out - 0.08)]
        add_face(f, cap, BEAM, [(0, 0), (2 * out, 0), (2 * out, 0.08), (0, 0.08)], (0.0, 1.0, 0.0))
        # Two house drapes under the box, hung on a gilt rod 0.25 m out from the posts.
        dw, dh, cut = hs - 0.05, min(9.5, box_floor - 3.0), 0.7
        top = box_floor - 0.3
        zc = hs + 0.25
        for x0 in (-hs + 0.05, 0.05):
            x1 = x0 + dw - 0.1
            xm = (x0 + x1) / 2
            outline = [w(x0, top - dh, zc), w(xm, top - dh + cut, zc), w(x1, top - dh, zc), w(x1, top, zc),
                       w(x0, top, zc)]
            _cloth(f, outline, BANNERS[house], nrm)
        mk.rod(m, w(-hs - 0.1, top - 0.03, zc), w(hs + 0.1, top - 0.03, zc), 0.04, 4, "metal", GILT)
    # The roof: a slate pyramid from the eaves to the apex, its underside in planks.
    re = out + TOWER_EAVES - 0.2
    apex = height + TOWER_APEX - 0.25
    corners = [(-re, re), (re, re), (re, -re), (-re, -re)]
    for i in range(4):
        (xa, za), (xb, zb) = corners[i], corners[(i + 1) % 4]
        tri = [(xa, eaves, za), (xb, eaves, zb), (0.0, apex, 0.0)]
        add_face(f, tri, roof_tag, _roof_uv(roof_tag, tri), (xa + xb, re, za + zb))
        # The underside, seen from the box and the ground: planks up to just under the apex.
        under = [(xa, eaves - 0.02, za), (xb, eaves - 0.02, zb), (0.0, apex - 0.2, 0.0)]
        add_face(f, under, PLANKS, [(0, 0), (2 * re, 0), (re, 0.6)], (-(xa + xb), -re, -(za + zb)))
    # Roof posts at the box's corners (the main posts carry on), the finial.
    mk.rod(m, (0.0, apex - 0.05, 0.0), (0.0, apex + 0.18, 0.0), 0.03, 4, "metal", GILT)
    mk.lathe(m, [(0.0, apex + 0.12), (0.07, apex + 0.17), (0.0, height + TOWER_APEX)], 6, "metal", GILT)
    k = from_mesh(m, ZONES)
    p.faces.extend(k.faces)
    p.meta.update(size=size, height=height, box_floor=box_floor, eaves=eaves, apex=height + TOWER_APEX,
                  reach=re, house=house)
    return p


def tower(house: float = 0, size: float = 4.0, height: float = 20.0, slates: float = 1) -> Piece:
    """`kit_pitch_tower` (module doc); its LOD1 drops the cross braces and
    the turned finial's detail."""
    h = int(round(house))
    if h not in (0, 1, 2, 3):
        raise ValueError("kit_pitch_tower: house 0..3 (house_a..house_d)")
    if not TOWER_SIZE[0] - 1e-9 <= size <= TOWER_SIZE[1] + 1e-9:
        raise ValueError(f"kit_pitch_tower: size must be within {TOWER_SIZE}")
    if not TOWER_HEIGHT[0] - 1e-9 <= height <= TOWER_HEIGHT[1] + 1e-9:
        raise ValueError(f"kit_pitch_tower: height must be within {TOWER_HEIGHT}")
    tag = ROOF if slates else SHINGLES
    p = _tower(h, size, height, True, tag)
    p.lod1 = _tower(h, size, height, False, tag).faces
    return p


# -- kit_pitch_stand ----------------------------------------------------------------------------


def _stand(length: float, depth: float, deck: float, roof: float, steps: float, steps_at: int, fine: bool,
           roof_tag: str = ROOF) -> Piece:
    p = Piece("", walkthrough=False)
    f = p.faces
    m = mk.Mesh()
    L, D, H = length, depth, deck
    X = (0.0, 0.0, 0.0)
    ex, ez, ey = (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0)
    nz = (0.0, 0.0, -1.0)
    # The deck (walkable: its top is exactly y = deck), the planks along the stand.
    _boards(f, (0.0, H, 0.0), ex, nz, L, D, ey)
    # Boarded down to the ground: the front, the back and the ends (under the deck).
    _boards(f, (0.0, -0.3, 0.0), ex, ey, L, H + 0.3, ez)
    _boards(f, (L, -0.3, -D), (-1.0, 0.0, 0.0), ey, L, H + 0.3, nz)
    for x, nrm in ((0.0, (-1.0, 0.0, 0.0)), (L, (1.0, 0.0, 0.0))):
        _boards(f, (x, -0.3, -D), ez, ey, D, H + 0.3, nrm)
    # The back wall over the deck, outside and in, up to the roof's back edge.
    back_top = H + roof + STAND_ROOF_RISE * (D / (D + STAND_ROOF_FRONT)) - 0.05
    _boards(f, (L, H, -D - 0.12), (-1.0, 0.0, 0.0), ey, L, back_top - H, nz, s0=0.4)
    _boards(f, (0.0, H, -D + 0.02), ex, ey, L, back_top - H - 0.02, ez, s0=0.9)
    # Its top and ends.
    add_face(f, [(0.0, back_top, -D + 0.02), (L, back_top, -D + 0.02), (L, back_top, -D - 0.12), (0.0, back_top, -D - 0.12)],
             BEAM, [(0, 0), (L, 0), (L, 0.14), (0, 0.14)], ey)
    for x, sgn in ((0.0, -1.0), (L, 1.0)):
        add_face(f, [(x, H, -D - 0.12), (x, H, -D + 0.02), (x, back_top, -D + 0.02), (x, back_top, -D - 0.12)], BEAM,
                 [(0, 0), (0.14, 0), (0.14, back_top - H), (0, back_top - H)], (sgn, 0.0, 0.0))
    # The roof: slates on top, planks under it, a fascia board along its low front edge.
    zf, zb = STAND_ROOF_FRONT, -D - 0.25
    yf, yb = H + roof, H + roof + STAND_ROOF_RISE
    xa, xb = -0.3, L + 0.3
    slope = math.hypot(zf - zb, yb - yf)
    u = ((zb - zf) / slope, (yb - yf) / slope)
    if roof_tag == ROOF:
        add_face(f, [(xa, yf, zf), (xb, yf, zf), (xb, yb, zb), (xa, yb, zb)], ROOF, None, (0.0, 1.0, 0.1))
    else:
        _boards(f, (xa, yf, zf), ex, (0.0, u[1], u[0]), xb - xa, slope, (0.0, 1.0, 0.1), s0=0.7, tag=roof_tag)
    _boards(f, (xb, yf - 0.12, zf), (-1.0, 0.0, 0.0), (0.0, u[1], u[0]), xb - xa, slope, (0.0, -1.0, 0.0))
    add_face(f, [(xa, yf - 0.12, zf), (xb, yf - 0.12, zf), (xb, yf, zf), (xa, yf, zf)], BEAM,
             [(0, 0), (xb - xa, 0), (xb - xa, 0.12), (0, 0.12)], ez)
    for x, sgn in ((xa, -1.0), (xb, 1.0)):
        add_face(f, [(x, yf - 0.12, zf), (x, yb - 0.12, zb), (x, yb, zb), (x, yf, zf)], BEAM,
                 [(0, 0), (slope, 0), (slope, 0.12), (0, 0.12)], (sgn, 0.0, 0.0))
    # Roof posts along the front and a beam under the roof's front edge.
    bays = max(1, round(L / 6.0))
    for i in range(bays + 1):
        x = min(max(L * i / bays, 0.12), L - 0.12)
        mk.box(m, (x - 0.11, H, -0.33), (x + 0.11, yf - 0.34, -0.11), "wood", OAK_AGED, skip=("-y", "+y"))
    mk.box(m, (0.0, yf - 0.34, -0.36), (L, yf - 0.1, -0.08), "wood", OAK_AGED, skip=("-x", "+x") if fine else ())
    # The back wall's outside: framing posts on each bay line and, in every other bay, a
    # house banner (the castle's painted banners, house_a..d in turn) for the view from
    # the castle and the lake.
    by = -D - 0.12
    for i in range(bays + 1):
        x = min(max(L * i / bays, 0.1), L - 0.1)
        mk.box(m, (x - 0.1, -0.3, by - 0.1), (x + 0.1, back_top, by), "wood", OAK_AGED, skip=("-y", "+z"))
    bh = back_top - 1.3
    bw_ = min(bh * 0.26, L / bays - 0.6)
    for i in range(bays):
        if i % 2:
            continue
        xm = L * (i + 0.5) / bays
        y1, y0 = back_top - 0.35, back_top - 0.35 - bh
        outline = [(xm + bw_ / 2, y0, by - 0.05), (xm, y0 + 0.5, by - 0.05), (xm - bw_ / 2, y0, by - 0.05),
                   (xm - bw_ / 2, y1, by - 0.05), (xm + bw_ / 2, y1, by - 0.05)]
        _cloth(f, outline, BANNERS[(i // 2) % 4], nz)
    # The front railing: a top rail and a mid rail on posts, low enough for a seated
    # spectator to see over (the fine build only: the far view keeps the posts' beam).
    if fine:
        n = max(1, round(L / 1.65))
        for i in range(n + 1):
            x = min(max(L * i / n, 0.05), L - 0.05)
            mk.box(m, (x - 0.045, H, -0.09), (x + 0.045, H + 0.85, -0.0), "wood", OAK, skip=("-y",))
        for y, hgt in ((H + 0.8, 0.09), (H + 0.42, 0.06)):
            mk.box(m, (0.0, y - hgt / 2, -0.11), (L, y + hgt / 2, 0.02), "wood", OAK, skip=("-x", "+x"))
    # House hangings along the front fascia: a swallowtail panel per bay, house_a..d in turn.
    nb = max(1, round(L / 4.0))
    bw = L / nb
    for i in range(nb):
        x0, x1 = i * bw + 0.35, (i + 1) * bw - 0.35
        xm = (x0 + x1) / 2
        q = [(x0, H - 1.6, 0.04), (xm, H - 1.25, 0.04), (x1, H - 1.6, 0.04), (x1, H - 0.1, 0.04), (x0, H - 0.1, 0.04)]
        m.poly(q, "fabric", zone=HOUSES[i % 4])
    if fine:
        mk.box(m, (0.0, H - 0.12, 0.0), (L, H - 0.02, 0.08), "metal", GILT, skip=("-x", "+x", "-z"))
    # The steps at an end or both: treads rising to the deck (their tops on the layout's ramp at their middles).
    ends = [e for e in (1, -1) if steps_at == 0 or steps_at == e] if steps > 0 else []
    for end in ends:
        n = max(2, round(H / 0.2))
        rise, going = H / n, steps / n
        sgn = float(end)
        x_end = L if end > 0 else 0.0  # where the flight meets the deck

        def sx(d):  # distance d from the deck's edge outward -> local x
            return x_end + sgn * d

        for k in range(n):
            d0, d1 = steps - (k + 1) * going, steps - k * going  # tread k: d0 nearer the deck
            top = (k + 0.5) * rise
            xa_, xb_ = sorted((sx(d0), sx(d1)))
            _boards(f, (xa_, top, 0.0), ex, nz, xb_ - xa_, D, ey, s0=0.2 * k)
            # The riser at the tread's outer (lower) edge.
            xr = sx(d1)
            prev = (k - 0.5) * rise if k > 0 else -0.3
            add_face(f, [(xr, prev, 0.0), (xr, top, 0.0), (xr, top, -D), (xr, prev, -D)], PLANKS,
                     [(0, 0), (0, top - prev), (D, top - prev), (D, 0)], (sgn, 0.0, 0.0))
        # The stringers' outer faces (both sides) down to the ground: a stepped outline.
        for z, nrm in ((0.0, ez), (-D, nz)):
            pts = [(sx(steps), -0.3, z)]
            for k in range(n):
                d0, d1 = steps - (k + 1) * going, steps - k * going
                top = (k + 0.5) * rise
                pts += [(sx(d1), top, z), (sx(d0), top, z)]
            pts += [(sx(0.0), -0.3, z)]
            if fine:
                add_face(f, pts, BEAM, [(q[0], max(0.0, min(0.6, (q[1] + 0.3) * 0.25))) for q in pts], nrm)
            else:
                add_face(f, [(sx(steps), -0.3, z), (sx(0.0), H, z), (sx(0.0), -0.3, z)], BEAM,
                         [(0, 0), (steps, 0.6), (steps, 0)], nrm)
        # Handrails on both sides on posts.
        if fine:
            for z in (0.06, -D - 0.06):
                for d in (steps - 0.15, steps / 2, 0.15):
                    y0 = max(0.0, (steps - d) / steps * H)
                    mk.box(m, (sx(d) - 0.045, y0 - 0.05, z - 0.045), (sx(d) + 0.045, y0 + 0.95, z + 0.045), "wood", OAK,
                           skip=("-y",))
                mk.rod(m, (sx(steps), 0.95, z), (sx(0.0), H + 0.95, z), 0.035, 4, "wood", OAK)
    k = from_mesh(m, ZONES)
    p.faces.extend(k.faces)
    p.meta.update(length=L, depth=D, deck=H, roof_front=yf, roof_back=yb, roof_reach=(xa, xb, zb, zf), steps=steps,
                  steps_at=steps_at)
    return p


def stand(length: float = 66.0, depth: float = 5.0, deck: float = 2.4, roof: float = 3.8, steps: float = 4.0,
          steps_at: float = 1, slates: float = 1) -> Piece:
    """`kit_pitch_stand` (module doc); its LOD1 drops the railing, the
    stepped stringers and the handrails."""
    if length < 4.0 or not 3.0 <= depth <= 8.0 or not 1.0 <= deck <= 4.0 or not 2.6 <= roof <= 5.0:
        raise ValueError("kit_pitch_stand: length 4 m or more, depth 3-8 m, deck 1-4 m, roof 2.6-5 m over the deck")
    sa = 0 if steps_at == 0 else (1 if steps_at > 0 else -1)
    tag = ROOF if slates else SHINGLES
    p = _stand(length, depth, deck, roof, steps, sa, True, tag)
    p.lod1 = _stand(length, depth, deck, roof, steps, sa, False, tag).faces
    return p


# -- kit_pitch_gate -----------------------------------------------------------------------------


def gate(width: float = 3.2, height: float = 3.9, slates: float = 1) -> Piece:
    """`kit_pitch_gate` (module doc)."""
    if not 2.0 <= width <= 5.0 or not 3.0 <= height <= 5.0:
        raise ValueError("kit_pitch_gate: width 2-5 m, height 3-5 m")
    p = Piece("", walkthrough=True)
    f = p.faces
    m = mk.Mesh()
    hw = width / 2.0
    for sx in (-1.0, 1.0):
        x = sx * (hw + 0.17)
        mk.box(m, (x - 0.17, -0.3, -0.17), (x + 0.17, height, 0.17), "wood", OAK_AGED, skip=("-y",))
        mk.lathe(m, [(0.12, height), (0.14, height + 0.08), (0.0, height + 0.3)], 6, "metal", GILT, (x, 0.0, 0.0))
        # Two iron straps.
        for y in (0.6, height - 0.6):
            mk.box(m, (x - 0.18, y, -0.18), (x + 0.18, y + 0.06, 0.18), "metal", IRON, skip=("-y", "+y"))
    lo, hi = height - 0.75, height - 0.4
    tag = ROOF if slates else SHINGLES
    mk.box(m, (-hw - 0.55, lo, -0.2), (hw + 0.55, hi, 0.2), "wood", OAK, skip=())
    # The slate cap over the lintel.
    for sz in (-1.0, 1.0):
        q = [(-hw - 0.7, hi, sz * 0.38), (hw + 0.7, hi, sz * 0.38), (hw + 0.7, hi + 0.32, 0.0), (-hw - 0.7, hi + 0.32, 0.0)]
        add_face(f, q, tag, _roof_uv(tag, q), (0.0, 1.0, sz))
    for sx in (-1.0, 1.0):
        add_face(f, [(sx * (hw + 0.7), hi, -0.38), (sx * (hw + 0.7), hi, 0.38), (sx * (hw + 0.7), hi + 0.32, 0.0)], BEAM,
                 [(0, 0), (0.76, 0), (0.38, 0.32)], (sx, 0.0, 0.0))
    # Four house pennants hanging from the lintel's front.
    pw = (width - 0.3) / 4.0
    for i in range(4):
        x0 = -hw + 0.15 + i * pw + 0.06
        x1 = x0 + pw - 0.12
        q = [(x0, lo - 0.02, 0.22), (x1, lo - 0.02, 0.22), ((x0 + x1) / 2, lo - 0.85, 0.22)]
        m.poly(list(reversed(q)) if mk.newell(q)[2] < 0 else q, "fabric", zone=HOUSES[i])
    k = from_mesh(m, ZONES, walkthrough=True)
    p.faces.extend(k.faces)
    p.meta.update(width=width, height=height)
    return p


# -- kit_pitch_apron ----------------------------------------------------------------------------


def apron(x0: float = -30.0, z0: float = -20.0, x1: float = 30.0, z1: float = 20.0, inner: float = 0.6,
          outer: float = 60.0, drop: float = 0.05) -> Piece:
    """`kit_pitch_apron` (module doc): four trapezoids round the rectangle."""
    p = Piece("", walkthrough=True)
    y = -drop
    a = [(x0 - inner, z0 - inner), (x1 + inner, z0 - inner), (x1 + inner, z1 + inner), (x0 - inner, z1 + inner)]
    b = [(x0 - outer, z0 - outer), (x1 + outer, z0 - outer), (x1 + outer, z1 + outer), (x0 - outer, z1 + outer)]
    for i in range(4):
        j = (i + 1) % 4
        q = [(a[i][0], y, a[i][1]), (b[i][0], y, b[i][1]), (b[j][0], y, b[j][1]), (a[j][0], y, a[j][1])]
        add_face(p.faces, q, TURF, None, (0.0, 1.0, 0.0))
    return p


BUILDERS = {
    "kit_pitch_hoop": hoop_piece,
    "kit_pitch_tower": tower,
    "kit_pitch_stand": stand,
    "kit_pitch_gate": gate,
    "kit_pitch_apron": apron,
}
CATALOGUE = {
    "kit_pitch_hoop": {},
    "kit_pitch_tower": {"house": 2},
    "kit_pitch_stand": {"length": 12.0},
    "kit_pitch_gate": {},
    "kit_pitch_apron": {"x0": -4.0, "z0": -3.0, "x1": 4.0, "z1": 3.0, "outer": 6.0},
}

# The practice field takes the upper rooms' rules and lint, as the paddock does
# (grid.m5b_rules reads INSTANCE_RULES).
G.INSTANCE_RULES["class_pitch"] = "upper_rooms"
