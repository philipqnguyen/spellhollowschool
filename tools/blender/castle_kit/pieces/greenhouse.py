"""Pieces of the Herbology class room,
`class_greenhouse` (tools/blender/world/school_rooms_greenhouse.py,
docs/architecture.md).

* `kit_glasshouse`: the glasshouse over the room's low stone wall: glazed
  side walls and gables standing on the wall's cap, a pitched glass roof on
  a ridge running north-south, thin iron glazing bars (mullions, transoms,
  rafters, purlins, the ridge and the gutters), three iron king-post
  trusses across the house. The panes are opaque, palette-tinted glass (the
  props atlas's glass tile in `glasshouse_glass`), facing in: the room is
  seen from inside only and needs no sky. Frame: origin on the floor at the
  room's middle, yaw 0; the glazing stands GLAZING_OUT outside the low
  wall's inner faces. Walk-through (nobody reaches it).
* `kit_glasshouse_porch`: the stone entrance bay in the south gable that
  carries the class door (the room's blind door stands in its hole, as in
  the Astronomy stair head, instance_kit.KitRoom.head_door). Frame as a
  wall piece: x along the face, y up, z out of the face into the room;
  origin on the face at the floor under the door's middle; the block runs
  `depth` behind the face. Stone in castle_wall (world UVs).
* `kit_potting_bench`: an oak potting bench facing +z (the students' side),
  top POTTING_BENCH_TOP up, a back board, a slatted shelf under it, and per
  student place a clay pot of earth with a seedling; spare pots and a seed
  tray on the shelf.
* `kit_plant_staging`: the low staging bench before the professor with the
  class plants' big clay pots, their earth PLANT_SOIL_Y over the floor (the
  plants themselves are the animated props `plant_*`, exported on their
  own: school_rooms_greenhouse.py CLASS_PROPS).

Frames as props.json `kit`: origin at the bottom centre unless said,
yaw 0 facing +z. Colours are palette names only (castle_textures.json
`palette`, each with its props_slots row; no colour literal here)."""

from __future__ import annotations

import math

import meshkit as mk
from castle_kit import grid as G
from castle_kit.pieces.geom import Piece, add_face
from castle_kit.pieces.hall import from_mesh

IRON, GLASS, OAK, CLAY, SOIL, LEAF, LEAF_LIGHT, PALE = range(1, 9)
ZONES = {IRON: "iron", GLASS: "glasshouse_glass", OAK: "oak_aged", CLAY: "terracotta", SOIL: "soil", LEAF: "plant",
         LEAF_LIGHT: "leaf_light", PALE: "parcel_tan"}
TILE = {IRON: "metal", GLASS: "glass", OAK: "wood", CLAY: "leather", SOIL: "leather", LEAF: "leaf", LEAF_LIGHT: "leaf",
        PALE: "fabric"}

GLAZING_OUT = 0.12  # the glazing's plane outside the low wall's inner face (on its 0.5 m cap)
BAY = 0.8  # the glazing bars' target pitch
BAR_R = 0.028  # a glazing bar's half thickness
GLASSHOUSE_TRIS = 4_000
PORCH_TRIS = 400
POTTING_BENCH_TOP = 0.9
POTTING_BENCH_TRIS = 900
PLACE_PITCH = 0.8  # a student's place along a potting bench
PLANT_STAGING_TOP = 0.55
PLANT_SOIL_Y = 0.9  # the class plants stand on their pots' earth (the layout's plant_* landmarks)
PLANT_POT_R = 0.3  # the class plants' pots' rim radius
PLANT_STAGING_TRIS = 900
WALL = "castle_wall|world"


def _rod(m, a, b, r, zone, seg=4, caps=False):
    mk.rod(m, a, b, r, seg, TILE[zone], zone, caps=caps)


def _box(m, lo, hi, zone, skip=("-y",)):
    mk.box(m, lo, hi, TILE[zone], zone, skip=skip)


def _splits(a: float, b: float, pitch: float) -> list[float]:
    """Bar positions from a to b (both ends included), about `pitch` apart."""
    n = max(1, int(round((b - a) / pitch)))
    return [a + (b - a) * k / n for k in range(n + 1)]


# -- kit_glasshouse ----------------------------------------------------------------------------


def glasshouse(width: float = 20.0, depth: float = 16.0, sill: float = 1.0, eaves: float = 3.6, rise: float = 4.6,
               door: float = 6.0, door_top: float = 5.6, trusses: float = 3) -> Piece:
    """`kit_glasshouse` over a `width` x `depth` floor (x by z) walled to
    `sill`: glazed walls to `eaves`, the roof's ridge `rise` over the eaves
    along z at x 0; the south gable (+z) leaves `door` m in its middle to
    the porch up to `door_top`. At most GLASSHOUSE_TRIS."""
    if not (8.0 <= width <= 30.0 and 8.0 <= depth <= 30.0 and 0.3 <= sill < eaves - 1.5 and 1.0 <= rise <= 8.0):
        raise ValueError("kit_glasshouse: width and depth 8..30 m, the sill under the eaves, rise 1..8 m")
    if not (0.0 <= door < width - 4.0 and (door == 0.0 or eaves < door_top < eaves + rise * (1 - door / width))):
        raise ValueError("kit_glasshouse: the door bay must fit the south gable under its roof line")
    m = mk.Mesh()
    hx, hz = width / 2.0 + GLAZING_OUT, depth / 2.0 + GLAZING_OUT
    ridge = eaves + rise
    transom = sill + 0.62 * (eaves - sill)

    def roof_y(x: float) -> float:
        return eaves + rise * (1.0 - abs(x) / hx)

    def pane(pts):
        m.poly(pts, TILE[GLASS], zone=GLASS)

    # The side walls (x = +-hx, along z): panes facing in, split by the transom.
    zs = _splits(-hz, hz, BAY)
    for sx in (-1.0, 1.0):
        x = sx * hx
        for z0, z1 in zip(zs, zs[1:]):
            for y0, y1 in ((sill, transom), (transom, eaves)):
                pts = [(x, y0, z0), (x, y0, z1), (x, y1, z1), (x, y1, z0)]
                pane(pts if sx > 0 else list(reversed(pts)))
        for z in zs[1:-1]:
            _rod(m, (x, sill, z), (x, eaves, z), BAR_R, IRON)
        for y in (sill, transom):
            _rod(m, (x, y, -hz), (x, y, hz), BAR_R, IRON)
        # The gutter plate along the eaves.
        _box(m, (min(x, x - sx * 0.14), eaves - 0.14, -hz), (max(x, x - sx * 0.14), eaves, hz), IRON, skip=())
    # The gables (z = +-hz, along x): panes up to the roof line; the south one leaves the door bay.
    hd = door / 2.0
    for sz in (-1.0, 1.0):
        z = sz * hz
        if sz > 0 and door > 0:
            xs = _splits(-hx, -hd, BAY) + _splits(-hd, hd, BAY)[1:] + _splits(hd, hx, BAY)[1:]
        else:
            xs = _splits(-hx, hx, BAY)
        for x0, x1 in zip(xs, xs[1:]):
            over_door = sz > 0 and door > 0 and -hd - 1e-9 <= x0 and x1 <= hd + 1e-9
            bands = [(door_top, None)] if over_door else [(sill, transom), (transom, eaves), (eaves, None)]
            for y0, y1 in bands:
                if y1 is None:  # up to the roof line (a peak over the ridge)
                    top = [(x1, roof_y(x1), z)]
                    if x0 < 0.0 < x1:
                        top.append((0.0, ridge, z))
                    top.append((x0, roof_y(x0), z))
                    pts = [(x0, y0, z), (x1, y0, z)] + top
                else:
                    pts = [(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)]
                pane(list(reversed(pts)) if sz > 0 else pts)
        for x in xs[1:-1]:
            on_door = sz > 0 and door > 0 and -hd - 1e-9 <= x <= hd + 1e-9
            _rod(m, (x, door_top if on_door else sill, z), (x, roof_y(x), z), BAR_R, IRON)
        for y in (sill, transom, eaves):
            if sz > 0 and door > 0 and y < door_top:
                for a, b in ((-hx, -hd), (hd, hx)):
                    _rod(m, (a, y, z), (b, y, z), BAR_R, IRON)
            else:
                _rod(m, (-hx, y, z), (hx, y, z), BAR_R, IRON)
        if sz > 0 and door > 0:
            _rod(m, (-hd, door_top, z), (hd, door_top, z), BAR_R, IRON)
        for sx in (-1.0, 1.0):  # the barge bars along the roof line
            _rod(m, (sx * hx, eaves, z), (0.0, ridge, z), BAR_R * 1.4, IRON)
    # The corner posts.
    for sx in (-1.0, 1.0):
        for sz in (-1.0, 1.0):
            _rod(m, (sx * hx, sill, sz * hz), (sx * hx, eaves, sz * hz), 0.05, IRON, seg=6)
    # The roof: two slopes from the eaves to the ridge, panes facing down into the room.
    slope = [eaves + rise * t for t in (0.0, 0.36, 0.7, 1.0)]  # the purlins' heights
    for sx in (-1.0, 1.0):
        for z0, z1 in zip(zs, zs[1:]):
            for ya, yb in zip(slope, slope[1:]):
                xa, xb = sx * hx * (1.0 - (ya - eaves) / rise), sx * hx * (1.0 - (yb - eaves) / rise)
                pts = [(xa, ya, z0), (xa, ya, z1), (xb, yb, z1), (xb, yb, z0)]
                pane(list(reversed(pts)) if sx > 0 else pts)
        for z in zs[1:-1]:  # the rafters
            _rod(m, (sx * hx, eaves, z), (0.0, ridge, z), BAR_R, IRON)
        for y in slope[1:-1]:  # the purlins
            x = sx * hx * (1.0 - (y - eaves) / rise)
            _rod(m, (x, y, -hz), (x, y, hz), BAR_R * 1.3, IRON)
    _box(m, (-0.08, ridge - 0.16, -hz), (0.08, ridge, hz), IRON, skip=("+y",))  # the ridge beam
    # The king-post trusses across the house, on rafters.
    n = int(round(trusses))
    for k in range(n):
        z = zs[int(round((k + 1) * (len(zs) - 1) / (n + 1)))]
        tie = eaves - 0.07
        _rod(m, (-hx + 0.1, tie, z), (hx - 0.1, tie, z), 0.035, IRON, seg=6)
        _rod(m, (0.0, tie, z), (0.0, ridge - 0.16, z), 0.035, IRON, seg=6)
        for sx in (-1.0, 1.0):
            _rod(m, (0.0, tie, z), (sx * hx * 0.5, roof_y(hx * 0.5) - 0.05, z), 0.03, IRON, seg=6)
            _rod(m, (sx * hx * 0.5, tie, z), (sx * hx * 0.5, roof_y(hx * 0.5) - 0.05, z), 0.025, IRON, seg=6)
    p = from_mesh(m, ZONES, walkthrough=True)
    p.meta.update(eaves=eaves, ridge=ridge, glazing=(hx, hz), trusses=n, tie=eaves - 0.07)
    return p


# -- kit_glasshouse_porch ----------------------------------------------------------------------


def glasshouse_porch(width: float = 6.0, height: float = 5.6, depth: float = 0.8, door_w: float = 3.0,
                     door_h: float = 4.5) -> Piece:
    """`kit_glasshouse_porch`: a stone face `width` x `height` with the
    door's hole (`door_w` x `door_h` from the floor), its sides and flat
    top running `depth` behind the face. At most PORCH_TRIS."""
    if not (door_w <= width - 2 * G.OPENING_MARGIN and door_h + G.STAIR_HEAD_LINTEL <= height and 0.3 <= depth <= 2.0):
        raise ValueError("kit_glasshouse_porch: the door must fit its face with a margin each side and a lintel over it")
    p = Piece("")
    f = p.faces
    hw, hd = width / 2.0, door_w / 2.0

    def quad(pts, n):
        add_face(f, list(pts), WALL, None, n)

    front = (0.0, 0.0, 1.0)
    quad([(-hw, 0.0, 0.0), (-hd, 0.0, 0.0), (-hd, height, 0.0), (-hw, height, 0.0)], front)
    quad([(hd, 0.0, 0.0), (hw, 0.0, 0.0), (hw, height, 0.0), (hd, height, 0.0)], front)
    quad([(-hd, door_h, 0.0), (hd, door_h, 0.0), (hd, height, 0.0), (-hd, height, 0.0)], front)
    for sx in (-1.0, 1.0):
        x = sx * hw
        quad([(x, 0.0, 0.0), (x, 0.0, -depth), (x, height, -depth), (x, height, 0.0)], (sx, 0.0, 0.0))
    quad([(-hw, height, 0.0), (hw, height, 0.0), (hw, height, -depth), (-hw, height, -depth)], (0.0, 1.0, 0.0))
    p.meta.update(door=(-hd, hd, door_h), size=(width, depth, height), top=height)
    return p


# -- kit_potting_bench -------------------------------------------------------------------------


def _jitter(seed: int, k: int) -> float:
    """A small deterministic value in -1..1 for a seed and a place."""
    s = (int(seed) * 2654435761 + k * 40503 + 97) % 2**32
    return ((s >> 9) % 2001 - 1000) / 1000.0


def _seedling(m, x: float, y: float, z: float, seed: int, k: int, scale: float = 1.0) -> None:
    """Three or four leaf blades fanning up from (x, y, z), double-sided."""
    n = 3 + (seed + k) % 2
    for i in range(n):
        a = 2 * math.pi * i / n + _jitter(seed, k * 7 + i) * 0.5
        ca, sa = math.cos(a), math.sin(a)
        h = (0.12 + 0.05 * _jitter(seed, k * 11 + i)) * scale
        tip = (x + 0.11 * scale * ca, y + h, z + 0.11 * scale * sa)
        mid = (x + 0.05 * scale * ca, y + h * 0.6, z + 0.05 * scale * sa)
        side = (-sa * 0.035 * scale, 0.0, ca * 0.035 * scale)
        blade = [(x, y, z), (mid[0] + side[0], mid[1], mid[2] + side[2]), tip, (mid[0] - side[0], mid[1], mid[2] - side[2])]
        zone = LEAF if (i + k) % 2 else LEAF_LIGHT
        m.poly(blade, TILE[zone], zone=zone)
        m.poly(list(reversed(blade)), TILE[zone], zone=zone)


def _clay_pot(m, x: float, y: float, z: float, r: float, h: float, seg: int = 8) -> None:
    """A clay flowerpot of rim radius r and height h standing at (x, y, z),
    earth a little under its rim."""
    mk.lathe(m, [(0.0, 0.0), (r * 0.72, 0.0), (r * 0.92, h * 0.82), (r, h * 0.84), (r, h), (r * 0.9, h)], seg, TILE[CLAY],
             CLAY, (x, y, z), cap_top=False)
    mk.lathe(m, [(r * 0.9, h), (r * 0.86, h * 0.9), (0.0, h * 0.9)], seg, TILE[SOIL], SOIL, (x, y, z), cap_top=False)


def potting_bench(length: float = 4.0, places: float = 5, seed: float = 1, grown: float = 0) -> Piece:
    """`kit_potting_bench`: `length` (1.6-4.8 m) of oak bench 0.8 m deep,
    its top POTTING_BENCH_TOP up, `places` (0..length // PLACE_PITCH) pots
    with seedlings along its front (`grown` 0..1: young plants up to
    about three times their size), spare pots and a seed tray on the
    shelf. Walk-through. At most POTTING_BENCH_TRIS."""
    n = int(round(places))
    if not (1.6 <= length <= 4.8 and 0 <= n <= int(length / PLACE_PITCH + 1e-9) and 0.0 <= grown <= 1.0):
        raise ValueError("kit_potting_bench: length 1.6..4.8 m, at most one place per PLACE_PITCH, grown 0..1")
    sd = int(round(seed))
    m = mk.Mesh()
    hl, top, d = length / 2.0, POTTING_BENCH_TOP, 0.4
    _box(m, (-hl, top - 0.06, -d), (hl, top, d), OAK)
    _box(m, (-hl, top - 0.06, -d - 0.03), (hl, top + 0.24, -d), OAK, skip=("-y",))  # the back board
    for x in (-hl + 0.06, hl - 0.06) + ((0.0,) if length > 3.0 else ()):
        for z in (-d + 0.06, d - 0.06):
            _box(m, (x - 0.04, 0.0, z - 0.04), (x + 0.04, top - 0.06, z + 0.04), OAK, skip=("-y", "+y"))
    for i in range(3):  # the slatted shelf
        z0 = -d + 0.08 + i * 0.22
        _box(m, (-hl + 0.1, 0.24, z0), (hl - 0.1, 0.27, z0 + 0.16), OAK)
    for k in range(n):
        x = (k - (n - 1) / 2.0) * PLACE_PITCH + 0.06 * _jitter(sd, k)
        z = 0.08 + 0.05 * _jitter(sd, k + 50)
        _clay_pot(m, x, top, z, 0.11, 0.17)
        _seedling(m, x, top + 0.153, z, sd, k, 1.0 + 2.0 * grown)
    # Spare pots stacked on the shelf and a seed tray of earth.
    for j, x in enumerate((-hl * 0.55, hl * 0.35)):
        for s in range(3):
            mk.lathe(m, [(0.08, 0.27 + 0.05 * s), (0.11, 0.27 + 0.05 * s + 0.14)], 8, TILE[CLAY], CLAY, (x, 0.0, -0.05),
                     cap_top=False)
    _box(m, (hl * 0.55 - 0.25, 0.27, -0.15), (hl * 0.55 + 0.25, 0.33, 0.17), OAK)
    _box(m, (hl * 0.55 - 0.22, 0.33, -0.12), (hl * 0.55 + 0.22, 0.32, 0.14), SOIL, skip=("-y",))
    p = from_mesh(m, ZONES, walkthrough=True)
    p.meta.update(top=top, places=[[round((k - (n - 1) / 2.0) * PLACE_PITCH, 4), round(d + 0.45, 4)] for k in range(n)])
    return p


# -- kit_plant_staging -------------------------------------------------------------------------


def plant_staging(length: float = 8.4, count: float = 4, pitch: float = 2.0) -> Piece:
    """`kit_plant_staging`: a low oak staging bench `length` long, 0.7 m
    deep, its top PLANT_STAGING_TOP up, carrying `count` big clay pots
    `pitch` apart about its middle, their earth at PLANT_SOIL_Y (where the
    class plants stand). Walk-through. At most PLANT_STAGING_TRIS."""
    n = int(round(count))
    if not (2.0 <= length <= 10.0 and 1 <= n <= 6 and (n - 1) * pitch + 2 * PLANT_POT_R <= length):
        raise ValueError("kit_plant_staging: length 2..10 m, 1..6 pots that fit it")
    m = mk.Mesh()
    hl, top, d = length / 2.0, PLANT_STAGING_TOP, 0.35
    _box(m, (-hl, top - 0.06, -d), (hl, top, d), OAK)
    for x in (-hl + 0.06, -hl / 3.0, hl / 3.0, hl - 0.06):
        for z in (-d + 0.06, d - 0.06):
            _box(m, (x - 0.045, 0.0, z - 0.045), (x + 0.045, top - 0.06, z + 0.045), OAK, skip=("-y", "+y"))
    _box(m, (-hl + 0.1, 0.12, -d + 0.08), (hl - 0.1, 0.15, d - 0.08), OAK)  # the bottom board
    h = PLANT_SOIL_Y - top
    r = PLANT_POT_R
    xs = [(k - (n - 1) / 2.0) * pitch for k in range(n)]
    for x in xs:
        mk.lathe(m, [(0.0, 0.0), (r * 0.7, 0.0), (r * 0.93, h + 0.01), (r * 1.06, h + 0.02), (r * 1.06, h + 0.07),
                     (r * 0.95, h + 0.07)], 12, TILE[CLAY], CLAY, (x, top, 0.0), cap_top=False)
        mk.lathe(m, [(r * 0.95, h + 0.07), (r * 0.93, h + 0.02), (0.0, h)], 12, TILE[SOIL], SOIL, (x, top, 0.0),
                 cap_top=False)
    p = from_mesh(m, ZONES, walkthrough=True)
    p.meta.update(top=top, soil=PLANT_SOIL_Y, pots=[round(x, 4) for x in xs])
    return p
