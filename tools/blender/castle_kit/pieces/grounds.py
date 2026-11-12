"""Pieces of the grounds (docs/art/castle-kit.md "Exterior";
tools/blender/world/grounds_export.py places them). Our own plain designs
(the Groundskeeper's Cottage, the boathouse).

- `kit_grounds_hut`: the gamekeeper's round stone hut, whole (the 2d
  `kit_hut_corner` is its half seen over the paddock wall): rough stone walls
  (castle_wall, world UVs: the one pale cool outside stone), a shut oak
  door in the facet facing +z with its frame and iron ring, two small lit
  windows either side and one at the back, a conical thatched roof with deep
  eaves (straw on the fabric tile, a darker band at the eaves) and a stone
  chimney. Origin at the hut's centre on its floor (y 0); the walls start
  0.4 m below it so they meet sloping ground.
- `kit_woodpile`, `kit_water_butt`, `kit_bench_rustic`: things by the hut: a
  stack of split logs under a plank lid, an oak water butt with iron hoops, a
  plank bench on two log legs. Origin at the bottom centre, facing +z.
- `kit_boathouse`: a timber boathouse on a stone footing (castle_wall), its
  -x half on piles over the water, a slate roof (`kit_roof_slate`, ridge
  along x), the boat doors in the -x gable (shut), a shut plank door in the
  +x gable, a slipway down from the boat doors and a short jetty beyond it.
  Origin at the middle of its floor; x along the ridge, the +x gable the
  land side. `floor` is the floor's height over the ground at the land door,
  `drop` how far the footing and the piles reach below the floor.
- `kit_standing_stone`: a rough monolith `height` tall, leaning a little,
  sunk 0.3 m into the ground (the props atlas's metal tile, a weathered
  stone colour by `seed`).
- `kit_drystone_wall`: a low rough wall `length` long (castle_wall, world
  UVs) with a cap of set stones; frame as `kit_fence_rail` (x along the run,
  the wall centred on z = 0), 0.3 m below y 0 so it meets the slope.
- `kit_tree_pine`: a conifer `height` tall (vegetation look A: a planting
  of the foliage kit's `pine_a` or `pine_b` by `seed`, world/foliage.py; no
  faces of its own), its lowest needles at least `TREE_CROWN_CLEAR` up.
  Origin at the trunk's foot.
- `kit_rock`: a lumpy boulder about `size` across, flattened, sunk a fifth of
  its height (metal tile in a weathered stone colour by `seed`).
- `kit_glasshouse_garden`: the 2d glasshouse (`kit_glasshouse`) seen from
  outside: every pane drawn both ways (the class room's panes face in), the
  house narrowed along x by `scale` (the 2d piece is at least 8 m wide), no
  door bay (the Greenhouse Gate stands before the gable).

Colours: palette names only (castle_textures.json `palette` and its
existing props_slots rows; no slot is added): oak, oak_aged, owl_brown
(leather), parcel_tan, food_bread and canvas (fabric), plant and leaf_light
(leaf), iron, steel_dark, beast_stone and statue_stone (metal), soot
(stone), glow_candle (glow), water (glass). Stone walls are the cell's
castle_wall (the export resolves it to `castle_wall_c@ext`), roofs
castle_roof. No colour literal here."""

from __future__ import annotations

import math

import foliage as FO
import meshkit as mk
from castle_kit import grid as G
from castle_kit.pieces import facades, greenhouse, paddock
from castle_kit.pieces.geom import KFace, Piece, add_face
from castle_kit.pieces.hall import from_mesh

WALL = "castle_wall|world"

(OAK, OAK_AGED, WEATHERED, STRAW, STRAW_DARK, CANVAS, LEAF, LEAF_LIGHT, IRON, STONE_A, STONE_B, STONE_PALE, SOOT,
 GLOW, WATER) = range(1, 16)
ZONES = {OAK: "oak", OAK_AGED: "oak_aged", WEATHERED: "owl_brown", STRAW: "parcel_tan", STRAW_DARK: "food_bread",
         CANVAS: "canvas", LEAF: "plant", LEAF_LIGHT: "leaf_light", IRON: "iron", STONE_A: "beast_stone",
         STONE_B: "steel_dark", STONE_PALE: "statue_stone", SOOT: "soot", GLOW: "glow_candle", WATER: "water"}
TILE = {OAK: "wood", OAK_AGED: "wood", WEATHERED: "leather", STRAW: "fabric", STRAW_DARK: "fabric", CANVAS: "fabric",
        LEAF: "leaf", LEAF_LIGHT: "leaf", IRON: "metal", STONE_A: "metal", STONE_B: "metal", STONE_PALE: "metal",
        SOOT: "stone", GLOW: "glow", WATER: "glass"}

# Caps (tests/test_grounds.py).
HUT_RADIUS = (3.5, 5.0)
HUT_TRIS = 1_400
SMALL_TRIS = 300  # each of the woodpile, the water butt and the bench
BOATHOUSE_TRIS = 2_400
STONE_TRIS = 80
WALL_TRIS_PER_M = 14
PINE_HEIGHT = (8.0, 15.0)  # vegetation look A: under 8 m the scaled crown would hang under TREE_CROWN_CLEAR
TREE_CROWN_CLEAR = 2.4  # the crown's underside over the ground (walk-through trees)
ROCK_TRIS = 80
GLASSHOUSE_GARDEN_TRIS = 7_000


def _rand(seed: int, i: int) -> float:
    return paddock._rand(seed, i)


def _box(m, lo, hi, zone, skip=("-y",)):
    mk.box(m, lo, hi, TILE[zone], zone, skip=skip)


def _rod(m, a, b, r, zone, seg=6, caps=True):
    mk.rod(m, a, b, r, seg, TILE[zone], zone, caps=caps)


def _facing(q, nrm):
    return q if mk.dot(mk.newell(q), nrm) > 0 else list(reversed(q))


# -- kit_grounds_hut ---------------------------------------------------------------------------


def grounds_hut(radius: float = 4.4, wall: float = 3.2, roof: float = 8.4, seg: float = 10) -> Piece:
    """`kit_grounds_hut` (module doc): the door in the facet facing +z."""
    if not HUT_RADIUS[0] - 1e-9 <= radius <= HUT_RADIUS[1] + 1e-9:
        raise ValueError(f"kit_grounds_hut: radius must be within {HUT_RADIUS}")
    if not 2.6 <= wall <= 4.0 or roof < wall + 2.0:
        raise ValueError("kit_grounds_hut: wall 2.6-4 m, the roof's tip at least 2 m over it")
    n = int(round(seg))
    if n % 2:
        raise ValueError("kit_grounds_hut: an even number of facets")
    p = Piece("")
    f = p.faces
    m = mk.Mesh()
    # Facet k spans angles a_k..a_k+1 measured from +z toward +x; facet 0 is centred on +z (the door).
    angs = [2 * math.pi * (i - 0.5) / n for i in range(n + 1)]
    ring = [(radius * math.sin(a), radius * math.cos(a)) for a in angs]
    windows = {2, n - 2}
    for i in range(n):
        (xa, za), (xb, zb) = ring[i], ring[i + 1]
        nrm = ((xa + xb) / 2, 0.0, (za + zb) / 2)
        add_face(f, [(xa, -0.4, za), (xb, -0.4, zb), (xb, wall, zb), (xa, wall, za)], WALL, None, nrm)
        mx, mz = (xa + xb) / 2, (za + zb) / 2
        ln = math.hypot(nrm[0], nrm[2])
        ox, oz = nrm[0] / ln * 0.03, nrm[2] / ln * 0.03
        el = math.hypot(xb - xa, zb - za)
        tx, tz = (xb - xa) / el, (zb - za) / el
        if i == 0:
            # The shut oak door, its frame, a lintel and an iron ring.
            w, h = 0.6, 2.2
            q = [(mx - tx * w + ox, 0.0, mz - tz * w + oz), (mx + tx * w + ox, 0.0, mz + tz * w + oz),
                 (mx + tx * w + ox, h, mz + tz * w + oz), (mx - tx * w + ox, h, mz - tz * w + oz)]
            m.poly(_facing(q, nrm), "wood", zone=OAK)
            for sgn in (-1.0, 1.0):
                cx, cz = mx + sgn * tx * (w + 0.06), mz + sgn * tz * (w + 0.06)
                _box(m, (cx - 0.08, 0.0, cz - 0.08), (cx + 0.08, h + 0.14, cz + 0.08), OAK_AGED)
            _rod(m, (mx - tx * (w + 0.2) + ox * 3, h + 0.2, mz - tz * (w + 0.2) + oz * 3),
                 (mx + tx * (w + 0.2) + ox * 3, h + 0.2, mz + tz * (w + 0.2) + oz * 3), 0.1, OAK_AGED, seg=4)
            _rod(m, (mx + tx * 0.35 + ox, 1.1, mz + tz * 0.35 + oz), (mx + tx * 0.35 + ox * 3, 1.1, mz + tz * 0.35 + oz * 3),
                 0.04, IRON, seg=6)
        elif i in windows:
            w, y0, y1 = 0.38, 1.4, 2.2
            q = [(mx - tx * w + ox, y0, mz - tz * w + oz), (mx + tx * w + ox, y0, mz + tz * w + oz),
                 (mx + tx * w + ox, y1, mz + tz * w + oz), (mx - tx * w + ox, y1, mz - tz * w + oz)]
            m.poly(_facing(q, nrm), "glow", zone=GLOW)
            _rod(m, (mx - tx * (w + 0.1) + ox, y0 - 0.05, mz - tz * (w + 0.1) + oz),
                 (mx + tx * (w + 0.1) + ox, y0 - 0.05, mz + tz * (w + 0.1) + oz), 0.06, OAK_AGED, seg=4)
            _rod(m, (mx + ox * 1.6, y0, mz + oz * 1.6), (mx + ox * 1.6, y1, mz + oz * 1.6), 0.03, OAK_AGED, seg=4)
    # The thatch: a cone from deep eaves to the tip, a darker band at the eaves, the eaves' underside.
    eave = radius + 0.8
    low, mid = wall - 0.35, wall + (roof - wall) * 0.22
    rm = eave * (roof - mid) / (roof - low)
    for i in range(n):
        a0, a1 = angs[i], angs[i + 1]
        s0, c0, s1, c1 = math.sin(a0), math.cos(a0), math.sin(a1), math.cos(a1)
        lo0, lo1 = (eave * s0, low, eave * c0), (eave * s1, low, eave * c1)
        md0, md1 = (rm * s0, mid, rm * c0), (rm * s1, mid, rm * c1)
        q = [lo0, lo1, md1, md0]
        m.poly(q if mk.newell(q)[1] > 0 else list(reversed(q)), "fabric", zone=STRAW_DARK)
        t = [md0, md1, (0.0, roof, 0.0)]
        m.poly(t if mk.newell(t)[1] > 0 else list(reversed(t)), "fabric", zone=STRAW)
        u = [lo0, lo1, (radius * s1, wall, radius * c1), (radius * s0, wall, radius * c0)]
        m.poly(u if mk.newell(u)[1] < 0 else list(reversed(u)), "wood", zone=OAK_AGED)
    # The chimney at the back (-z), stone through the thatch, a sooty cap.
    cx, cz, cw = 0.9, -radius + 1.3, 0.38
    for (xa, za), (xb, zb), nrm in (((cx - cw, cz + cw), (cx + cw, cz + cw), (0, 0, 1)),
                                    ((cx + cw, cz + cw), (cx + cw, cz - cw), (1, 0, 0)),
                                    ((cx + cw, cz - cw), (cx - cw, cz - cw), (0, 0, -1)),
                                    ((cx - cw, cz - cw), (cx - cw, cz + cw), (-1, 0, 0))):
        add_face(f, [(xa, wall, za), (xb, wall, zb), (xb, roof - 1.2, zb), (xa, roof - 1.2, za)], WALL, None, nrm)
    _box(m, (cx - cw - 0.05, roof - 1.2, cz - cw - 0.05), (cx + cw + 0.05, roof - 1.0, cz + cw + 0.05), SOOT)
    k = from_mesh(m, ZONES)
    p.faces.extend(k.faces)
    p.meta.update(radius=radius, wall_top=wall, roof=roof, eave=eave, door=(0.0, radius))
    return p


# -- things by the hut -------------------------------------------------------------------------


def woodpile(length: float = 1.8) -> Piece:
    """`kit_woodpile`: three courses of split logs `length` long under a plank lid."""
    m = mk.Mesh()
    hl = length / 2.0
    k = 0
    for row in range(3):
        n = 4 - row
        for c in range(n):
            z = (c - (n - 1) / 2.0) * 0.3
            y = 0.15 + row * 0.26
            _rod(m, (-hl, y, z), (hl, y, z), 0.13, OAK if (k % 3) else OAK_AGED, seg=5)
            k += 1
    _box(m, (-hl - 0.1, 0.92, -0.65), (hl + 0.1, 0.97, 0.65), WEATHERED, skip=())
    for sx in (-1.0, 1.0):
        _box(m, (sx * hl - 0.05, 0.0, -0.05), (sx * hl + 0.05, 0.92, 0.05), OAK_AGED)
    return from_mesh(m, ZONES, walkthrough=True)


def water_butt() -> Piece:
    """`kit_water_butt`: an oak butt 1 m tall with two iron hoops, water at its brim."""
    m = mk.Mesh()
    mk.lathe(m, [(0.34, 0.0), (0.4, 0.5), (0.36, 1.0)], 10, "wood", OAK_AGED, cap_top=False)
    mk.lathe(m, [(0.35, 0.96), (0.35, 0.96)], 10, "glass", WATER, cap_top=True)
    for y in (0.2, 0.8):
        r = 0.4 - abs(0.5 - y) * 0.12 + 0.01
        mk.lathe(m, [(r, y - 0.03), (r, y + 0.03)], 10, "metal", IRON, cap_top=False)
    return from_mesh(m, ZONES, walkthrough=True)


def bench_rustic(length: float = 2.0) -> Piece:
    """`kit_bench_rustic`: a thick plank on two log legs."""
    m = mk.Mesh()
    hl = length / 2.0
    _box(m, (-hl, 0.42, -0.2), (hl, 0.5, 0.2), OAK, skip=())
    for sx in (-1.0, 1.0):
        mk.lathe(m, [(0.17, 0.0), (0.16, 0.42)], 6, "wood", OAK_AGED, center=(sx * (hl - 0.3), 0.0, 0.0), cap_top=False)
    return from_mesh(m, ZONES, walkthrough=True)


# -- kit_boathouse -----------------------------------------------------------------------------


def boathouse(length: float = 13.0, width: float = 8.0, wall: float = 3.6, floor: float = 0.3, drop: float = 4.5,
              pitch: float = 50.0) -> Piece:
    """`kit_boathouse` (module doc): `length` along x (the -x half over the water),
    `width` across; the floor at y 0."""
    if not (8.0 <= length <= 16.0 and 5.0 <= width <= 9.0 and 2.8 <= wall <= 4.5 and 1.0 <= drop <= 8.0):
        raise ValueError("kit_boathouse: length 8-16 m, width 5-9 m, wall 2.8-4.5 m, drop 1-8 m")
    p = Piece("")
    f = p.faces
    m = mk.Mesh()
    hx, hz = length / 2.0, width / 2.0
    # The stone footing under the land half (+x), its top 0.15 under the floor.
    fx0 = 0.0
    for q, nrm in (([(fx0, -drop, hz), (hx, -drop, hz), (hx, -0.15, hz), (fx0, -0.15, hz)], (0, 0, 1)),
                   ([(hx, -drop, -hz), (fx0, -drop, -hz), (fx0, -0.15, -hz), (hx, -0.15, -hz)], (0, 0, -1)),
                   ([(hx, -drop, hz), (hx, -drop, -hz), (hx, -0.15, -hz), (hx, -0.15, hz)], (1, 0, 0))):
        add_face(f, q, WALL, None, nrm)
    # The timber walls: plank faces on the wood tile, corner posts, a sill beam.
    for q, nrm in (([(-hx, 0.0, hz), (hx, 0.0, hz), (hx, wall, hz), (-hx, wall, hz)], (0, 0, 1)),
                   ([(hx, 0.0, -hz), (-hx, 0.0, -hz), (-hx, wall, -hz), (hx, wall, -hz)], (0, 0, -1))):
        m.poly(_facing(q, nrm), "wood", zone=OAK_AGED)
    for sx in (-1.0, 1.0):
        x = sx * hx
        q = [(x, 0.0, hz), (x, 0.0, -hz), (x, wall, -hz), (x, wall, hz)]
        m.poly(_facing(q, (sx, 0, 0)), "wood", zone=OAK_AGED)
    for sx in (-1.0, 1.0):
        for sz in (-1.0, 1.0):
            _box(m, (sx * hx - 0.14, -0.3, sz * hz - 0.14), (sx * hx + 0.14, wall, sz * hz + 0.14), OAK)
    for sz in (-1.0, 1.0):
        _box(m, (-hx, -0.3, sz * hz - 0.12), (hx, 0.05, sz * hz + 0.12), OAK)
        for k in range(1, int(length // 3.2) + 1):  # posts along the long walls
            x = -hx + k * length / (int(length // 3.2) + 1)
            za, zb = sorted((sz * (hz - 0.05), sz * (hz + 0.1)))
            _box(m, (x - 0.1, 0.0, za), (x + 0.1, wall, zb), OAK)
    for sx in (-1.0, 1.0):
        _box(m, (sx * hx - 0.12, -0.3, -hz), (sx * hx + 0.12, 0.05, hz), OAK)
    # The boat doors in the -x gable (shut), a plank door in the +x gable, a lamp over it.
    ox = 0.04
    bw, bh = width * 0.36, wall - 0.5
    q = [(-hx - ox, 0.0, -bw), (-hx - ox, 0.0, bw), (-hx - ox, bh, bw), (-hx - ox, bh, -bw)]
    m.poly(_facing(q, (-1, 0, 0)), "wood", zone=OAK)
    _rod(m, (-hx - 0.1, bh + 0.05, -bw - 0.2), (-hx - 0.1, bh + 0.05, bw + 0.2), 0.1, OAK_AGED, seg=4)
    for z in (-bw, 0.0, bw):
        _box(m, (-hx - 0.12, 0.0, z - 0.07), (-hx, bh, z + 0.07), OAK_AGED)
    dw, dh = 0.6, 2.2
    q = [(hx + ox, 0.0, -dw), (hx + ox, 0.0, dw), (hx + ox, dh, dw), (hx + ox, dh, -dw)]
    m.poly(_facing(q, (1, 0, 0)), "wood", zone=OAK)
    for z in (-dw - 0.07, dw + 0.07):
        _box(m, (hx, 0.0, z - 0.07), (hx + 0.12, dh + 0.12, z + 0.07), OAK_AGED)
    _rod(m, (hx + ox, 1.1, 0.3), (hx + 0.14, 1.1, 0.3), 0.035, IRON)
    # Windows: two lit panes on each long side.
    for sz in (-1.0, 1.0):
        for x in (-hx * 0.45, hx * 0.45):
            q = [(x - 0.45, 1.5, sz * (hz + ox)), (x + 0.45, 1.5, sz * (hz + ox)), (x + 0.45, 2.3, sz * (hz + ox)),
                 (x - 0.45, 2.3, sz * (hz + ox))]
            m.poly(_facing(q, (0, 0, sz)), "glow", zone=GLOW)
    # Piles under the water half and the slipway and jetty beyond the boat doors.
    for x in (-hx, -hx * 0.5, 0.0):
        for sz in (-1.0, 0.0, 1.0):
            _rod(m, (x, -drop - 1.0, sz * (hz - 0.2)), (x, -0.3, sz * (hz - 0.2)), 0.16, OAK_AGED, seg=5, caps=False)
    jl = 4.0
    _box(m, (-hx - jl, -0.15, -1.2), (-hx, 0.0, 1.2), WEATHERED, skip=())
    for x in (-hx - jl + 0.2, -hx - jl * 0.5):
        for sz in (-1.0, 1.0):
            _rod(m, (x, -drop - 1.0, sz * 1.05), (x, 0.4, sz * 1.05), 0.12, OAK_AGED, seg=5, caps=True)
    slip = [(-hx, -0.2, -bw), (-hx, -0.2, bw), (-hx - jl * 0.9, -drop * 0.6, bw), (-hx - jl * 0.9, -drop * 0.6, -bw)]
    m.poly(_facing(slip, (0, 1, 0)), TILE[WEATHERED], zone=WEATHERED)
    k = from_mesh(m, ZONES)
    p.faces.extend(k.faces)
    # The slate roof over the walls, ridge along x, its gables stone.
    roof = facades.roof_slate(width, length, pitch=pitch, overhang=0.5, verge=0.4, thick=0.25, cornice=0.0)
    for fc in roof.faces:
        pts = [(x - hx, y + wall, z) for x, y, z in fc.pts]
        p.faces.append(KFace(pts, fc.tag, fc.uv, fc.anchor, fc.group))
    p.meta.update(length=length, width=width, wall=wall, land_door=(hx, 0.0))
    return p


# -- kit_standing_stone, kit_rock --------------------------------------------------------------


def _lumpy(m, seed: int, half_w: float, half_d: float, height: float, zone: int, seg: int, sink: float, taper: float):
    """A rough block about 2 `half_w` x 2 `half_d` across: a lathe of `seg` sides,
    its rings jittered by `seed` (the same point always moves the same way)."""
    mark = m.mark()
    prof = [(0.92, -sink), (1.0, 0.0), (0.92, height * 0.45), (0.8 * taper + 0.1, height * 0.85), (0.0, height)]
    mk.lathe(m, prof, seg, TILE[zone], zone, smooth=False, cap_top=False, phase=_rand(seed, 0) * math.pi)

    def jitter(q):
        x, y, z = q
        a = math.atan2(x, z)
        r = 1.0 + 0.2 * (_rand(seed, int((a + math.pi) * 3.0) + 7 * int(y * 3 + 9)) - 0.5)
        return (x * half_w * r, y, z * half_d * r)

    m.transform(mark, jitter)


def standing_stone(height: float = 3.0, seed: float = 0) -> Piece:
    """`kit_standing_stone` (module doc)."""
    if not 1.5 <= height <= 4.5:
        raise ValueError("kit_standing_stone: height 1.5-4.5 m")
    k = int(round(seed))
    m = mk.Mesh()
    _lumpy(m, k, 0.55 + 0.12 * _rand(k, 1), 0.36, height, (STONE_A, STONE_PALE, STONE_B)[k % 3], 6, 0.3, 0.75)
    lean = 0.05 * (_rand(k, 2) - 0.5)
    m.transform((0, 0), lambda q: (q[0] + q[1] * lean, q[1], q[2]))
    p = from_mesh(m, ZONES, walkthrough=True)
    p.meta.update(height=height)
    return p


def rock(size: float = 1.6, seed: float = 0) -> Piece:
    """`kit_rock` (module doc)."""
    if not 0.4 <= size <= 6.0:
        raise ValueError("kit_rock: size 0.4-6 m")
    k = int(round(seed))
    m = mk.Mesh()
    _lumpy(m, k, size * 0.5, size * 0.42, size * 0.55, (STONE_A, STONE_B)[k % 2], 7, size * 0.12, 1.0)
    p = from_mesh(m, ZONES, walkthrough=True)
    p.meta.update(size=size)
    return p


# -- kit_drystone_wall -------------------------------------------------------------------------


def drystone_wall(length: float = 8.0, height: float = 1.0, thick: float = 0.6) -> Piece:
    """`kit_drystone_wall` (module doc)."""
    if length <= 0.5 or not 0.5 <= height <= 1.6 or not 0.3 <= thick <= 1.0:
        raise ValueError("kit_drystone_wall: length over 0.5 m, height 0.5-1.6 m, thick 0.3-1 m")
    p = Piece("", walkthrough=True)
    f = p.faces
    t, b = thick / 2.0, thick / 2.0 + 0.06  # a little batter
    lo = -0.3
    add_face(f, [(0.0, lo, b), (length, lo, b), (length, height, t), (0.0, height, t)], WALL, None, (0, 0, 1))
    add_face(f, [(length, lo, -b), (0.0, lo, -b), (0.0, height, -t), (length, height, -t)], WALL, None, (0, 0, -1))
    for x, s in ((0.0, -1.0), (length, 1.0)):
        add_face(f, [(x, lo, -b), (x, lo, b), (x, height, t), (x, height, -t)], WALL, None, (s, 0, 0))
    m = mk.Mesh()
    n = max(1, int(round(length / 0.9)))
    for i in range(n):
        x0, x1 = i * length / n + 0.03, (i + 1) * length / n - 0.03
        hy = 0.16 + 0.06 * ((i * 7) % 3) / 2.0
        _box(m, (x0, height, -t - 0.03), (x1, height + hy, t + 0.03), STONE_B if i % 2 else STONE_A)
    k = from_mesh(m, ZONES)
    f.extend(k.faces)
    p.meta.update(length=length, height=height)
    return p


# -- kit_tree_pine -----------------------------------------------------------------------------


def tree_pine(height: float = 9.0, seed: float = 0) -> Piece:
    """`kit_tree_pine` (module doc): a planting of vegetation look A's pine (world/foliage.py),
    its top at `height`; no faces of its own."""
    if not PINE_HEIGHT[0] - 1e-9 <= height <= PINE_HEIGHT[1] + 1e-9:
        raise ValueError(f"kit_tree_pine: height must be within {PINE_HEIGHT}")
    k = int(round(seed))
    pl = FO.tree("pine", height, k)
    sp = FO.SPECIES[pl.species]
    p = Piece("", walkthrough=True)
    p.meta.update(height=round(height, 4), crown_low=round(sp.crown_low * pl.scale(), 4), foliage=[pl])
    return p


# -- kit_glasshouse_garden ---------------------------------------------------------------------


def glasshouse_garden(width: float = 8.0, depth: float = 12.0, eaves: float = 3.0, rise: float = 3.2,
                      scale: float = 1.0) -> Piece:
    """`kit_glasshouse_garden` (module doc)."""
    if not 0.6 <= scale <= 1.0:
        raise ValueError("kit_glasshouse_garden: scale 0.6-1")
    g = greenhouse.glasshouse(width=width, depth=depth, sill=0.6, eaves=eaves, rise=rise, door=0.0, door_top=0.0,
                              trusses=2)
    p = Piece("", walkthrough=True)
    glass = G.palette_tag("glass", "glasshouse_glass")
    for fc in g.faces:
        pts = [(x * scale, y, z) for x, y, z in fc.pts]
        p.faces.append(KFace(pts, fc.tag, fc.uv, fc.anchor, fc.group))
        if fc.tag == glass:
            p.faces.append(KFace(list(reversed(pts)), fc.tag, list(reversed(fc.uv)) if fc.uv else None, fc.anchor,
                                 fc.group))
    # The low stone sill wall the panes stand on (the class room's Planner wall, here a piece).
    hx, hz = (width / 2.0 + greenhouse.GLAZING_OUT) * scale, depth / 2.0 + greenhouse.GLAZING_OUT
    t = 0.25
    for q, nrm in (([(-hx - t, -0.4, hz + t), (hx + t, -0.4, hz + t), (hx + t, 0.6, hz + t), (-hx - t, 0.6, hz + t)], (0, 0, 1)),
                   ([(hx + t, -0.4, -hz - t), (-hx - t, -0.4, -hz - t), (-hx - t, 0.6, -hz - t), (hx + t, 0.6, -hz - t)], (0, 0, -1)),
                   ([(hx + t, -0.4, hz + t), (hx + t, -0.4, -hz - t), (hx + t, 0.6, -hz - t), (hx + t, 0.6, hz + t)], (1, 0, 0)),
                   ([(-hx - t, -0.4, -hz - t), (-hx - t, -0.4, hz + t), (-hx - t, 0.6, hz + t), (-hx - t, 0.6, -hz - t)], (-1, 0, 0)),
                   ([(-hx - t, 0.6, hz + t), (hx + t, 0.6, hz + t), (hx + t, 0.6, -hz - t), (-hx - t, 0.6, -hz - t)], (0, 1, 0))):
        add_face(p.faces, q, WALL, None, nrm)
    # LOD1: the sill wall and the glazing as plain planes (both ways), no bars.
    sill = [fc for fc in p.faces if fc.tag == WALL]
    gx, ridge = hx - 0.02, eaves + rise
    hull = []
    for sx in (-1.0, 1.0):
        hull.append(([(sx * gx, 0.6, -hz), (sx * gx, 0.6, hz), (sx * gx, eaves, hz), (sx * gx, eaves, -hz)], (sx, 0, 0)))
        hull.append(([(sx * gx, eaves, -hz), (sx * gx, eaves, hz), (0.0, ridge, hz), (0.0, ridge, -hz)], (sx, 1, 0)))
    for sz in (-1.0, 1.0):
        hull.append(([(-gx, 0.6, sz * hz), (gx, 0.6, sz * hz), (gx, eaves, sz * hz), (0.0, ridge, sz * hz),
                      (-gx, eaves, sz * hz)], (0, 0, sz)))
    lod = list(sill)
    for q, nrm in hull:
        add_face(lod, q, glass, None, nrm)
        add_face(lod, q, glass, None, (-nrm[0], -nrm[1], -nrm[2]))
    p.lod1 = lod
    p.meta.update(g.meta, half=(hx + t, hz + t))
    return p


BUILDERS = {
    "kit_grounds_hut": grounds_hut,
    "kit_woodpile": woodpile,
    "kit_water_butt": water_butt,
    "kit_bench_rustic": bench_rustic,
    "kit_boathouse": boathouse,
    "kit_standing_stone": standing_stone,
    "kit_rock": rock,
    "kit_drystone_wall": drystone_wall,
    "kit_tree_pine": tree_pine,
    "kit_glasshouse_garden": glasshouse_garden,
}
CATALOGUE = {
    "kit_grounds_hut": {},
    "kit_woodpile": {},
    "kit_water_butt": {},
    "kit_bench_rustic": {},
    "kit_boathouse": {},
    "kit_standing_stone": {"seed": 1},
    "kit_rock": {"seed": 1},
    "kit_drystone_wall": {"length": 8.0},
    "kit_tree_pine": {"seed": 1},
    "kit_glasshouse_garden": {"scale": 0.92},
}
