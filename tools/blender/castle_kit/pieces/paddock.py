"""Pieces of the Creature Keeping room,
`class_paddock` (tools/blender/world/school_rooms_paddock.py,
docs/architecture.md): an outdoor paddock open to the sky.

- `kit_fence_rail`: a post-and-rail fence `length` long, posts at both ends
  and at most `pitch` apart, `rails` rails of weathered timber (our own
  plain design). Frame: x along the run from its start, y up from the ground,
  the rails centred on z = 0. Walk-through (the server has no prop
  collision). At most grid budget FENCE_TRIS_PER_M a metre.
- `kit_stand_mark`: a flat patch of strewn straw on the grass where a
  student stands (y at most STAND_MARK_H, so it reads as floor). Origin at
  its centre.
- `kit_hedge_run`: a clipped hedge `length` long, `height` tall, `depth`
  deep (vegetation look A: plantings of the foliage kit's hedge pieces in
  meta["foliage"], world/foliage.py; the piece has no faces of its own).
  Frame as the fence's; it stands outside the paddock's low wall and hides
  the void.
- `kit_tree_round`: a round-crowned field tree `height` tall (vegetation
  look A: a planting of the kit's `broadleaf_round`, turned by `seed`).
  Origin at the trunk's foot.
- `kit_hut_corner`: the corner of the keeper's stone hut seen over the
  paddock wall: half a round hut of rough stone (castle_wall, world UVs)
  with an oak door, a lit window, a conical timber roof with deep eaves and
  a stone chimney. Origin at the hut's centre on the ground; the half
  faces +z.
- `kit_hay_bale` and `kit_trough`: dressing: a bound straw bale; a plank
  water trough on two trestles.

Colours: palette names only (grid.palette_tag; existing props_slots rows:
oak and oak_aged on the wood tile, owl_brown on the leather tile (the
fences' weathered timber), plant and leaf_light on the leaf tile,
parcel_tan, food_bread and canvas on the fabric tile, iron on the metal
tile, water on the glass tile, glow_candle on the glow tile, soot on the
stone tile); the stone is the room's castle_wall material. No props slot is
added. No colour literal here."""

from __future__ import annotations

import math

import foliage as FO
import meshkit as mk
from castle_kit import grid as G
from castle_kit.pieces.geom import Piece, add_face
from castle_kit.pieces.hall import from_mesh

WALL = "castle_wall|world"

OAK, OAK_AGED, LEAF, LEAF_LIGHT, STRAW, CANVAS, IRON, WATER, GLOW, SOOT, WEATHERED, GOLDEN = 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12
ZONES = {OAK: "oak", OAK_AGED: "oak_aged", LEAF: "plant", LEAF_LIGHT: "leaf_light", STRAW: "parcel_tan",
         CANVAS: "canvas", IRON: "iron", WATER: "water", GLOW: "glow_candle", SOOT: "soot", WEATHERED: "owl_brown",
         GOLDEN: "food_bread"}
TILE = {OAK: "wood", OAK_AGED: "wood", LEAF: "leaf", LEAF_LIGHT: "leaf", STRAW: "fabric", CANVAS: "fabric",
        IRON: "metal", WATER: "glass", GLOW: "glow", SOOT: "stone", WEATHERED: "leather", GOLDEN: "fabric"}

# Caps (checked by tests/test_class_paddock.py).
FENCE_HEIGHT = (0.7, 1.4)
FENCE_TRIS_PER_M = 30
STAND_MARK_H = 0.02  # under the class-room tests' 0.031 m floor band
STAND_MARK_TRIS = 24
TREE_HEIGHT = (3.5, 8.0)
HUT_RADIUS = (3.0, 5.0)
HUT_TRIS = 1_200
HAY_BALE_TRIS = 60
TROUGH_TRIS = 120


def _rand(seed: int, i: int) -> float:
    """A deterministic 0..1 from (seed, i)."""
    s = (int(seed) * 2654435761 + i * 40503 + 97) % 2**32
    s ^= s >> 13
    s = (s * 1274126177) % 2**32
    return (s >> 8) / float(1 << 24)


# -- kit_fence_rail ----------------------------------------------------------------------------


def fence_rail(length: float = 4.0, height: float = 1.1, rails: float = 3, pitch: float = 2.0) -> Piece:
    """`kit_fence_rail` (module doc)."""
    if length <= 0.2:
        raise ValueError("kit_fence_rail: length must be over 0.2 m")
    if not FENCE_HEIGHT[0] - 1e-9 <= height <= FENCE_HEIGHT[1] + 1e-9:
        raise ValueError(f"kit_fence_rail: height must be within {FENCE_HEIGHT}")
    n_rails = int(round(rails))
    if n_rails not in (2, 3):
        raise ValueError("kit_fence_rail: 2 or 3 rails")
    m = mk.Mesh()
    bays = max(1, math.ceil(length / pitch - 1e-9))
    step = length / bays
    post, cap = 0.06, 0.06  # half the post's side; the post stands this over the top rail
    for i in range(bays + 1):
        x = i * step
        mk.box(m, (x - post, 0.0, -post), (x + post, height, post), TILE[WEATHERED], WEATHERED, skip=("-y",))
    rail_h, rail_d = 0.11, 0.04
    top, low = height - cap - rail_h / 2, 0.3 if n_rails == 3 else height * 0.45
    tops = [top - k * (top - low) / (n_rails - 1) for k in range(n_rails)]
    for i in range(bays):
        x0, x1 = i * step + post, (i + 1) * step - post
        for y in tops:
            mk.box(m, (x0, y - rail_h / 2, post - rail_d), (x1, y + rail_h / 2, post), TILE[WEATHERED], WEATHERED,
                   skip=("-x", "+x"))
    p = from_mesh(m, ZONES, walkthrough=True)
    p.meta.update(length=length, height=height, posts=bays + 1)
    return p


# -- kit_stand_mark ----------------------------------------------------------------------------


def stand_mark(seed: float = 0, radius: float = 0.34) -> Piece:
    """`kit_stand_mark` (module doc): an irregular patch of strewn straw on
    the grass, a golden, smaller patch on it, both flat."""
    k = int(round(seed))
    m = mk.Mesh()
    n = 10
    turn = _rand(k, 0) * math.pi
    for layer, (scale, y, zone) in enumerate(((1.0, STAND_MARK_H - 0.004, STRAW), (0.6, STAND_MARK_H, GOLDEN))):
        pts = []
        for i in range(n):
            r = radius * scale * (0.85 + 0.3 * _rand(k, i + 1 + 20 * layer))
            a = turn + 2 * math.pi * i / n + 0.4 * layer
            pts.append((r * math.sin(a), y, r * math.cos(a)))
        if mk.newell(pts)[1] < 0:
            pts.reverse()
        m.poly(pts, "fabric", zone=zone)
    p = from_mesh(m, ZONES, walkthrough=True)
    p.meta.update(radius=radius)
    return p


# -- kit_hedge_run -----------------------------------------------------------------------------


def hedge_run(length: float = 6.0, height: float = 2.4, depth: float = 1.1, seed: float = 0) -> Piece:
    """`kit_hedge_run` (module doc): no faces of its own; vegetation look A's hedge
    (world/foliage.py `hedge_run`: rounded ends and stretched middle segments) as plantings in
    meta["foliage"], drawn by the client from the foliage kit."""
    if length <= 0.5 or not 1.0 <= height <= 4.0 or not 0.5 <= depth <= 2.0:
        raise ValueError("kit_hedge_run: length over 0.5 m, height 1-4 m, depth 0.5-2 m")
    p = Piece("", walkthrough=True)
    p.meta.update(length=length, height=height, foliage=FO.hedge_run(length, height, depth, int(round(seed))))
    return p


# -- kit_tree_round ----------------------------------------------------------------------------


def tree_round(height: float = 6.0, seed: float = 0) -> Piece:
    """`kit_tree_round` (module doc): a planting of vegetation look A's round broadleaf
    (world/foliage.py), its top at `height`, turned by `seed`; no faces of its own."""
    if not TREE_HEIGHT[0] - 1e-9 <= height <= TREE_HEIGHT[1] + 1e-9:
        raise ValueError(f"kit_tree_round: height must be within {TREE_HEIGHT}")
    p = Piece("", walkthrough=True)
    p.meta.update(height=round(height, 4), foliage=[FO.tree("round", height, int(round(seed)))])
    return p


# -- kit_hut_corner ----------------------------------------------------------------------------


def hut_corner(radius: float = 3.6, wall: float = 3.0, roof: float = 6.6, seg: float = 8) -> Piece:
    """`kit_hut_corner` (module doc): the half of the hut that faces +z,
    from x = -radius round to x = +radius; at most HUT_TRIS."""
    if not HUT_RADIUS[0] - 1e-9 <= radius <= HUT_RADIUS[1] + 1e-9:
        raise ValueError(f"kit_hut_corner: radius must be within {HUT_RADIUS}")
    if not 2.4 <= wall <= 4.0 or roof < wall + 1.5:
        raise ValueError("kit_hut_corner: wall 2.4-4 m, the roof's tip at least 1.5 m over it")
    n = int(round(seg))
    p = Piece("")
    f = p.faces
    m = mk.Mesh()
    angs = [math.pi * i / n for i in range(n + 1)]  # (cos, sin) as (x, z): +x round through +z to -x
    ring = [(radius * math.cos(a), radius * math.sin(a)) for a in angs]
    door_k = n // 2  # the door in the middle facet (facing +z), a window either side of it
    for i in range(n):
        (xa, za), (xb, zb) = ring[i], ring[i + 1]
        nrm = ((xa + xb) / 2, 0.0, (za + zb) / 2)
        add_face(f, [(xa, -0.3, za), (xb, -0.3, zb), (xb, wall, zb), (xa, wall, za)], WALL, None, nrm)
        mx, mz = (xa + xb) / 2, (za + zb) / 2
        ln = math.hypot(nrm[0], nrm[2])
        ox, oz = nrm[0] / ln * 0.03, nrm[2] / ln * 0.03
        tx, tz = (xb - xa) / math.hypot(xb - xa, zb - za), (zb - za) / math.hypot(xb - xa, zb - za)
        if i == door_k or i == door_k - 1:
            # The oak door, its frame and an iron ring (on the facet left of centre: the hut's door).
            if i == door_k - 1:
                w, h = 0.55, 2.1
                q = [(mx - tx * w + ox, 0.0, mz - tz * w + oz), (mx + tx * w + ox, 0.0, mz + tz * w + oz),
                     (mx + tx * w + ox, h, mz + tz * w + oz), (mx - tx * w + ox, h, mz - tz * w + oz)]
                m.poly(q if mk.dot(mk.newell(q), nrm) > 0 else list(reversed(q)), "wood", zone=OAK)
                for sgn in (-1.0, 1.0):
                    mk.box(m, (mx + sgn * tx * (w + 0.05) - 0.07, 0.0, mz + sgn * tz * (w + 0.05) - 0.07),
                           (mx + sgn * tx * (w + 0.05) + 0.07, h + 0.12, mz + sgn * tz * (w + 0.05) + 0.07), "wood",
                           OAK_AGED, skip=("-y",))
                mk.rod(m, (mx + tx * 0.3 + ox, 1.05, mz + tz * 0.3 + oz),
                       (mx + tx * 0.3 + ox * 2.5, 1.05, mz + tz * 0.3 + oz * 2.5), 0.035, 6, "metal", IRON)
            else:
                # A lit window: warm glass in an oak frame with a sill.
                w, y0, y1 = 0.45, 1.5, 2.4
                q = [(mx - tx * w + ox, y0, mz - tz * w + oz), (mx + tx * w + ox, y0, mz + tz * w + oz),
                     (mx + tx * w + ox, y1, mz + tz * w + oz), (mx - tx * w + ox, y1, mz - tz * w + oz)]
                m.poly(q if mk.dot(mk.newell(q), nrm) > 0 else list(reversed(q)), "glow", zone=GLOW)
                mk.rod(m, (mx - tx * (w + 0.1) + ox, y0 - 0.04, mz - tz * (w + 0.1) + oz),
                       (mx + tx * (w + 0.1) + ox, y0 - 0.04, mz + tz * (w + 0.1) + oz), 0.06, 4, "wood", OAK_AGED)
                mk.rod(m, (mx + ox * 1.5, y0, mz + oz * 1.5), (mx + ox * 1.5, y1, mz + oz * 1.5), 0.025, 4, "wood", OAK_AGED)
                mk.rod(m, (mx - tx * w + ox * 1.5, (y0 + y1) / 2, mz - tz * w + oz * 1.5),
                       (mx + tx * w + ox * 1.5, (y0 + y1) / 2, mz + tz * w + oz * 1.5), 0.025, 4, "wood", OAK_AGED)
    # The wall's top under the eaves, and the flat back across the cut (never seen from the paddock).
    add_face(f, [(radius, -0.3, 0.0), (-radius, -0.3, 0.0), (-radius, wall, 0.0), (radius, wall, 0.0)], WALL, None,
             (0.0, 0.0, -1.0))
    # The conical roof: deep eaves over the wall, timber on the wood tile, a darker ridge band.
    eave = radius + 0.7
    for i in range(n):
        a0, a1 = angs[i], angs[i + 1]
        lo0 = (eave * math.cos(a0), wall - 0.25, eave * math.sin(a0))
        lo1 = (eave * math.cos(a1), wall - 0.25, eave * math.sin(a1))
        mid0 = (eave * 0.35 * math.cos(a0), wall + (roof - wall) * 0.65, eave * 0.35 * math.sin(a0))
        mid1 = (eave * 0.35 * math.cos(a1), wall + (roof - wall) * 0.65, eave * 0.35 * math.sin(a1))
        q = [lo0, lo1, mid1, mid0]
        m.poly(q if mk.newell(q)[1] > 0 else list(reversed(q)), "wood", zone=OAK_AGED)
        t = [mid0, mid1, (0.0, roof, 0.0)]
        m.poly(t if mk.newell(t)[1] > 0 else list(reversed(t)), "wood", zone=OAK)
        # The eaves' underside.
        u = [lo0, lo1, (radius * math.cos(a1), wall, radius * math.sin(a1)), (radius * math.cos(a0), wall, radius * math.sin(a0))]
        m.poly(u if mk.newell(u)[1] < 0 else list(reversed(u)), "wood", zone=OAK)
    # The chimney (stone, sooty top) rising through the roof's back half.
    cx, cz, cw = -1.2, 0.9, 0.36
    for (xa, za), (xb, zb), nrm in (((cx - cw, cz + cw), (cx + cw, cz + cw), (0, 0, 1)),
                                    ((cx + cw, cz + cw), (cx + cw, cz - cw), (1, 0, 0)),
                                    ((cx - cw, cz - cw), (cx - cw, cz + cw), (-1, 0, 0))):
        add_face(f, [(xa, wall + 1.0, za), (xb, wall + 1.0, zb), (xb, roof + 0.4, zb), (xa, roof + 0.4, za)], WALL, None, nrm)
    mk.box(m, (cx - cw - 0.04, roof + 0.4, cz - cw - 0.04), (cx + cw + 0.04, roof + 0.55, cz + cw + 0.04), "stone", SOOT)
    k = from_mesh(m, ZONES)
    p.faces.extend(k.faces)
    p.meta.update(radius=radius, wall_top=wall, roof=roof)
    return p


# -- kit_hay_bale, kit_trough ------------------------------------------------------------------


def hay_bale() -> Piece:
    """`kit_hay_bale`: a straw bale 1 x 0.5 x 0.45 m with two canvas bands."""
    m = mk.Mesh()
    mk.box(m, (-0.5, 0.0, -0.25), (0.5, 0.45, 0.25), "fabric", STRAW, skip=("-y",))
    for x in (-0.25, 0.25):
        mk.box(m, (x - 0.025, 0.0, -0.255), (x + 0.025, 0.455, 0.255), "fabric", CANVAS, skip=("-y", "-x", "+x"))
    return from_mesh(m, ZONES, walkthrough=True)


def trough() -> Piece:
    """`kit_trough`: a plank water trough 1.6 m long on two trestles."""
    m = mk.Mesh()
    for x in (-0.6, 0.6):
        for sz in (-1.0, 1.0):
            mk.rod(m, (x, 0.0, sz * 0.25), (x, 0.42, sz * 0.1), 0.035, 4, "wood", OAK_AGED)
    mk.box(m, (-0.8, 0.4, -0.25), (0.8, 0.44, 0.25), "wood", OAK)
    for sz in (-1.0, 1.0):
        mk.box(m, (-0.8, 0.44, sz * 0.25 - 0.02), (0.8, 0.7, sz * 0.25 + 0.02), "wood", OAK)
    for sx in (-1.0, 1.0):
        mk.box(m, (sx * 0.8 - 0.02, 0.44, -0.23), (sx * 0.8 + 0.02, 0.7, 0.23), "wood", OAK_AGED)
    m.poly([(-0.78, 0.62, 0.23), (0.78, 0.62, 0.23), (0.78, 0.62, -0.23), (-0.78, 0.62, -0.23)], "glass", zone=WATER)
    return from_mesh(m, ZONES, walkthrough=True)


BUILDERS = {
    "kit_fence_rail": fence_rail,
    "kit_stand_mark": stand_mark,
    "kit_hedge_run": hedge_run,
    "kit_tree_round": tree_round,
    "kit_hut_corner": hut_corner,
    "kit_hay_bale": hay_bale,
    "kit_trough": trough,
}
CATALOGUE = {
    "kit_fence_rail": {"length": 6.0},
    "kit_stand_mark": {"seed": 1},
    "kit_hedge_run": {"length": 6.0, "seed": 1},
    "kit_tree_round": {"seed": 1},
    "kit_hut_corner": {},
    "kit_hay_bale": {},
    "kit_trough": {},
}

# The room takes the upper rooms' rules and lint, as the Astronomy platform does
# (grid.m5b_rules reads INSTANCE_RULES).
G.INSTANCE_RULES["class_paddock"] = "upper_rooms"


# -- the turf (castle_floor_grass, texture grass_turf) -----------------------------------------


def turf(size: int = 1024, seed: int = 2601):
    """The paddock's grass, tiling at castle_textures.json tile_m (4.8 m):
    world/palette.py CASTLE grass mottled toward CASTLE plant in broad
    patches, a finer clover-like dapple and a fine speckle of blades, kept inside
    the albedo range (paint.fit_albedo). Deterministic."""
    import numpy as np

    import paint
    import palette

    light, dark = paint.hex_rgb(palette.CASTLE["grass"]), paint.hex_rgb(palette.CASTLE["plant"])
    broad = paint.periodic_noise(size, size, (3, 3), seed, 3)
    patch = paint.smoothstep(0.42, 0.75, broad)
    dapple = paint.periodic_noise(size, size, (14, 14), seed + 1, 2) - 0.5
    blades = paint.periodic_noise(size, size, (96, 96), seed + 2, 2) - 0.5
    rgb = light[None, None, :] * (1.0 - 0.55 * patch[..., None]) + dark[None, None, :] * (0.55 * patch[..., None])
    rgb = rgb * (1.0 + dapple[..., None] * 0.16 + blades[..., None] * 0.14)
    return paint.fit_albedo(paint.rgba(np.asarray(rgb, dtype=np.float32)))
