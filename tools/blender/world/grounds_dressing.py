"""Where everything stands on the grounds (docs/art/castle-kit.md
"Exterior"). Pure Python, no `bpy`, deterministic.

    python3 -I tools/blender/world/grounds_dressing.py           # report
    python3 -I tools/blender/world/grounds_dressing.py --write   # rewrite the grounds_* keep-outs
    python3 -I tools/blender/world/grounds_dressing.py --check   # exit 1 if keep_out.json is stale

* `placements(g, ctx)`: the pieces each grounds cell draws (castle-kit pieces by
  catalogue name, at a position and yaw): the Groundskeeper's Cottage and the things by it,
  the greenhouses' three glasshouses, the boathouse, the stone circle, the
  hedges by the Greenhouse Gate, the paddock's fence either side of its gate,
  lamp posts by the courtyard gate and the boathouse, and the low dry-stone
  wall along the castle side (just off the walkable ground).
* `foliage(g, ctx)`: every tree and rock as an instance of one of
  `SHAPES` (the `foliage` cell): copses on the grounds, the old oak by its
  find spot, rocks on the Owl Loft knoll's flanks, the shore and the crag strip,
  and the deep forest's first rows of trees east of the walkable ground.
  A tree or rock stands off the paths (`PATH_CLEAR` from a path's centre),
  off the reserved sites, the buildings, the gates, the key places and the
  find spots (`SPOT_CLEAR`), and off the seams between walkable rectangles
  (`SEAM_CLEAR`: the trail's portals need room); one on walkable ground keeps
  its crown's underside at least `pieces.grounds.TREE_CROWN_CLEAR` up.
* `keep_outs(...)`: one entry (`grounds_<what>_<n>`) per walk-through obstacle
  standing on walkable ground: a trunk, a rock, a standing stone, a
  hedge or fence run, a lamp post, a thing by the hut. They go in
  `data/world/keep_out.json` (`--write`), where the load-test bots and the
  Monstra Viam trail walk round them.
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER = os.path.dirname(HERE)
for _p in (BLENDER, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import grounds_terrain as GT  # noqa: E402
import layout_lib as L  # noqa: E402

ROOT = os.path.abspath(os.path.join(BLENDER, "..", ".."))
LAYOUT_PATH = os.path.join(ROOT, "data", "world", "layout.json")
TERRAIN_DIR = os.path.join(ROOT, "data", "world", "terrain")
KEEP_OUT_PATH = os.path.join(ROOT, "data", "world", "keep_out.json")

PATH_CLEAR = 3.0  # trees and rocks stand this far from a path's centre line
SPOT_CLEAR = 4.0  # ... and from a gate, a key place, a landmark or a find spot
SEAM_CLEAR = 1.5  # an obstacle's keep-out grown by this never touches a seam between walkable rectangles
BUILDING_CLEAR = 1.0
MAX_KEEP_OUTS = 120
KEEP_OUT_PREFIX = "grounds_"

# The instanced shapes of the `foliage` cell: name -> (piece, args). One mesh each (one draw).
SHAPES = {
    "tree_round": ("kit_tree_round", {"height": 6.5, "seed": 1}),
    "tree_broad": ("kit_tree_round", {"height": 7.5, "seed": 2}),
    "tree_pine": ("kit_tree_pine", {"height": 9.0, "seed": 1}),
    "rock": ("kit_rock", {"size": 1.6, "seed": 1}),
}
# Trunk half-widths (m, at scale 1) and the crown's underside (m) of the trees.
TRUNK = {"tree_round": 0.4, "tree_broad": 0.45, "tree_pine": 0.35}
CROWN_LOW = {"tree_round": 2.6, "tree_broad": 2.9, "tree_pine": 2.8}


def _rand(seed: int, i: int) -> float:
    s = (int(seed) * 2654435761 + i * 40503 + 97) % 2**32
    s ^= s >> 13
    s = (s * 1274126177) % 2**32
    return (s >> 8) / float(1 << 24)


@dataclass
class Placed:
    """A castle-kit piece on the grounds: world position (its frame's origin) and yaw."""

    id: str
    piece: str
    args: dict
    pos: tuple
    yaw: float
    cell: str
    keep_out: tuple | None = None  # (x0, z0, x1, z1) when it stands on walkable ground
    occluder: tuple | None = None  # (x0, z0, x1, z1) darkening the terrain at its foot
    rise: float = 0.0  # a run (wall, hedge, fence) climbs this much over its length (the export shears it)


@dataclass
class Instance:
    """One instanced tree or rock of the `foliage` cell."""

    id: str
    shape: str
    pos: tuple
    yaw: float
    scale: float
    keep_out: tuple | None = None


@dataclass
class Context:
    """The facts the rules read (built once from the grounds and the layout)."""

    g: GT.Grounds
    spots: list  # (x, z) of gates, key places' landmarks, landmarks and find spots on the grounds
    reserved: list  # grounds.json reserved entries
    buildings: list  # (x0, z0, x1, z1)
    seams: list  # shared edges between walkable rectangles: (axis, c, t0, t1)
    paths: list  # (half width, points)
    notes: list = field(default_factory=list)


def context(g: GT.Grounds, layout) -> Context:
    spots = []
    for e in list(layout.landmarks) + list(layout.interactables):
        x, _, z = e["pos"] if isinstance(e, dict) else e.pos
        if g.x0 <= x <= g.x0 + (g.nx - 1) * g.step and g.z0 <= z <= g.z0 + (g.nz - 1) * g.step:
            spots.append((x, z))
    buildings = [(b["min"][0], b["min"][1], b["max"][0], b["max"][1]) for b in g.meta["buildings"]]
    seams = []
    rects = [(x0, z0, x1, z1) for x0, z0, x1, z1, _ in g.walk]
    for a in range(len(rects)):
        for b in range(a + 1, len(rects)):
            ax0, az0, ax1, az1 = rects[a]
            bx0, bz0, bx1, bz1 = rects[b]
            for c in {ax0, ax1} & {bx0, bx1}:
                if (c == ax1 and c == bx0) or (c == ax0 and c == bx1):
                    t0, t1 = max(az0, bz0), min(az1, bz1)
                    if t1 > t0:
                        seams.append(("x", c, t0, t1))
            for c in {az0, az1} & {bz0, bz1}:
                if (c == az1 and c == bz0) or (c == az0 and c == bz1):
                    t0, t1 = max(ax0, bx0), min(ax1, bx1)
                    if t1 > t0:
                        seams.append(("z", c, t0, t1))
    paths = [(float(p["width"]) / 2.0, [tuple(q) for q in p["points"]]) for p in g.meta["paths"].values()]
    return Context(g, spots, list(g.meta["reserved"]), buildings, seams, paths)


def cell_of(g: GT.Grounds, x: float, z: float) -> str:
    n = g.cell_size
    c = min(max(int((x - g.x0) // n), 0), int(round((g.nx - 1) * g.step / n)) - 1)
    r = min(max(int((z - g.z0) // n), 0), int(round((g.nz - 1) * g.step / n)) - 1)
    return f"grounds_r{r}_c{c}"


def _in_reserved(ctx: Context, x: float, z: float, margin: float) -> bool:
    for r in ctx.reserved:
        if "centre" in r:
            if math.hypot(x - r["centre"][0], z - r["centre"][1]) < r["radius"] + margin:
                return True
        elif r["min"][0] - margin < x < r["max"][0] + margin and r["min"][1] - margin < z < r["max"][1] + margin:
            return True
    return False


def _hits_seam(ctx: Context, rect, grow: float) -> bool:
    x0, z0, x1, z1 = rect[0] - grow, rect[1] - grow, rect[2] + grow, rect[3] + grow
    for axis, c, t0, t1 in ctx.seams:
        if axis == "x" and x0 < c < x1 and z0 < t1 and z1 > t0:
            return True
        if axis == "z" and z0 < c < z1 and x0 < t1 and x1 > t0:
            return True
    return False


def free_spot(ctx: Context, x: float, z: float, half: float, why: list | None = None) -> bool:
    """May a tree or rock of footprint half-width `half` stand at (x, z)?"""
    reasons = []
    if any(GT.polyline_dist(x, z, pts) < PATH_CLEAR + half for _, pts in ctx.paths):
        reasons.append("path")
    if _in_reserved(ctx, x, z, 1.0 + half):
        reasons.append("reserved")
    if any(bx0 - BUILDING_CLEAR - half < x < bx1 + BUILDING_CLEAR + half and bz0 - BUILDING_CLEAR - half < z < bz1 +
           BUILDING_CLEAR + half for bx0, bz0, bx1, bz1 in ctx.buildings):
        reasons.append("building")
    if any(math.hypot(x - sx, z - sz) < SPOT_CLEAR + half for sx, sz in ctx.spots):
        reasons.append("spot")
    rect = (x - half, z - half, x + half, z + half)
    if ctx.g.walkable(x, z) and _hits_seam(ctx, rect, 0.45 + SEAM_CLEAR):
        reasons.append("seam")
    if why is not None:
        why.extend(reasons)
    return not reasons


def _ground(g: GT.Grounds, x: float, z: float) -> float:
    return round(g.height(x, z), 3)


def _ko(x: float, z: float, hx: float, hz: float) -> tuple:
    return (round(x - hx, 2), round(z - hz, 2), round(x + hx, 2), round(z + hz, 2))


def _rot_rect(x: float, z: float, lx: float, lz: float, yaw: float) -> tuple:
    """The plan rectangle round a piece of local half-extents (lx, lz) turned by `yaw`."""
    c, s = abs(math.cos(yaw)), abs(math.sin(yaw))
    return _ko(x, z, lx * c + lz * s, lx * s + lz * c)


def _run_rect(x: float, z: float, yaw: float, length: float, half_depth: float) -> tuple:
    """The plan rectangle of a run from (x, z) along local +x turned by `yaw`."""
    dx, dz = math.cos(yaw) * length, -math.sin(yaw) * length
    xs, zs = (x, x + dx), (z, z + dz)
    return (round(min(xs) - half_depth, 2), round(min(zs) - half_depth, 2), round(max(xs) + half_depth, 2),
            round(max(zs) + half_depth, 2))


def _along(g: GT.Grounds, x0: float, x1: float, z: float, seg: float):
    """A run along x at `z` cut into lengths of about `seg`: (x, x end, y at x, rise to the end)."""
    n = max(1, int(math.ceil((x1 - x0) / seg - 1e-9)))
    out = []
    for k in range(n):
        xa, xb = x0 + (x1 - x0) * k / n, x0 + (x1 - x0) * (k + 1) / n
        ya, yb = g.height(xa, z), g.height(xb, z)
        out.append((round(xa, 3), round(xb, 3), round(ya, 3), round(yb - ya, 3)))
    return out


# -- the pieces --------------------------------------------------------------------------------

HUT = (99.0, 177.0)
GLASSHOUSES = ((34.0, {"eaves": 2.8, "rise": 2.6}), (42.0, {"eaves": 3.2, "rise": 3.4}), (50.0, {"eaves": 2.8, "rise": 2.6}))
GLASSHOUSE_Z = 107.0
GLASSHOUSE_SCALE = 0.84
BOATHOUSE = (-98.8, 202.0)  # its floor's middle; length 9.6 along x, the land door at x -94.0
BOATHOUSE_ARGS = {"length": 9.6, "width": 8.0, "wall": 3.4, "drop": 4.0}
STONE_CIRCLE = (104.0, 112.0, 9.0)
STONE_GAPS = ((-math.pi / 2, 0.75), (math.pi, 0.4))  # (angle from +x toward +z, half width): north (the bridge), west (the path)
STONE_STEP = 2 * math.pi / 17
CASTLE_WALL_Z = 91.6  # the dry-stone wall's centre line, just north of the walkable ground (z 92)
CASTLE_WALL_RUNS = ((-104.0, -19.6), (51.6, 97.6), (110.4, 116.0))
HEDGE_Z = 99.5
HEDGE_RUNS = ((31.0, 38.6), (45.4, 55.0))
FENCE_Z = 190.5
FENCE_RUNS = ((87.0, 95.3), (104.7, 113.0))
LAMPS = (("gate_w", 10.0, 94.2), ("gate_e", 22.0, 94.2), ("boathouse", -93.0, 199.2))
WALL_SEG, FENCE_SEG = 6.0, 4.4  # runs on sloping ground are cut into lengths that follow the ground


def placements(ctx: Context) -> list[Placed]:
    g = ctx.g
    out: list[Placed] = []
    # The Groundskeeper's Cottage: its floor on the flattened site, the door (local +z) facing west, the walls deep
    # enough to meet the lowest ground under them.
    hx, hz = HUT
    floor = float(g.meta["sites"]["groundskeepers_cottage"]["y"])
    low = min(g.height(hx + 4.4 * math.cos(a), hz + 4.4 * math.sin(a)) for a in [k * math.pi / 12 for k in range(24)])
    out.append(Placed("hut", "kit_grounds_hut", {"radius": 4.4}, (hx, floor, hz), -math.pi / 2, cell_of(g, hx, hz),
                      occluder=(hx - 4.6, hz - 4.6, hx + 4.6, hz + 4.6)))
    ctx.notes.append(f"hut floor {floor}, lowest ground under its wall {low:.2f}")
    for pid, piece, x, z, yaw, half in (("woodpile", "kit_woodpile", 104.9, 177.0, math.pi / 2, (1.0, 0.65)),
                                        ("water_butt", "kit_water_butt", 93.2, 174.0, 0.0, (0.42, 0.42)),
                                        ("bench", "kit_bench_rustic", 97.4, 171.3, math.pi, (1.0, 0.25))):
        ko = _rot_rect(x, z, half[0], half[1], yaw)
        out.append(Placed(pid, piece, {}, (x, _ground(g, x, z), z), yaw, cell_of(g, x, z), ko, ko))
    # The greenhouses: three glasshouses in a row, ridges along z, on the flattened site.
    gy = float(g.meta["sites"]["greenhouses"]["y"])
    for i, (x, args) in enumerate(GLASSHOUSES):
        out.append(Placed(f"glasshouse_{i}", "kit_glasshouse_garden", dict(args, scale=GLASSHOUSE_SCALE),
                          (x, gy, GLASSHOUSE_Z), 0.0, cell_of(g, 42.0, GLASSHOUSE_Z),
                          occluder=(x - 3.7, GLASSHOUSE_Z - 6.4, x + 3.7, GLASSHOUSE_Z + 6.4)))
    # The boathouse: the land door at its +x gable, the floor a little over the ground there.
    bx, bz = BOATHOUSE
    door_x = bx + BOATHOUSE_ARGS["length"] / 2.0
    by = round(g.height(door_x + 0.5, bz) + 0.25, 3)
    out.append(Placed("boathouse", "kit_boathouse", dict(BOATHOUSE_ARGS), (bx, by, bz), 0.0, cell_of(g, bx, bz),
                      occluder=(bx - 4.8, bz - 4.0, bx + 4.8, bz + 4.0)))
    # The stone circle: standing stones round the flattened site, gaps north and west.
    cx, cz, rad = STONE_CIRCLE
    k = 0
    for i in range(int(round(2 * math.pi / STONE_STEP))):
        a = -math.pi + i * STONE_STEP + STONE_STEP / 2
        if any(abs(math.atan2(math.sin(a - ga), math.cos(a - ga))) < gw for ga, gw in STONE_GAPS):
            continue
        x, z = cx + rad * math.cos(a), cz + rad * math.sin(a)
        h = 2.6 + 1.2 * _rand(31, i)
        yaw = -a + math.pi / 2  # the stone's broad face toward the centre
        ko = _rot_rect(x, z, 0.7, 0.45, yaw)
        out.append(Placed(f"stone_{k}", "kit_standing_stone", {"height": round(h, 2), "seed": k},
                          (round(x, 3), _ground(g, x, z), round(z, 3)), round(yaw, 4), cell_of(g, x, z), ko, ko))
        k += 1
    # The dry-stone wall along the castle side, just off the walkable ground.
    for n, (x0, x1) in enumerate(CASTLE_WALL_RUNS):
        for seg, (x, xe, y, rise) in enumerate(_along(g, x0, x1, 92.0, WALL_SEG)):
            out.append(Placed(f"wall_{n}_{seg}", "kit_drystone_wall", {"length": round(xe - x, 3), "height": 1.0},
                              (round(x, 3), y, CASTLE_WALL_Z), 0.0, cell_of(g, x + 0.1, 92.0),
                              occluder=(x, CASTLE_WALL_Z - 0.4, xe, CASTLE_WALL_Z + 0.4), rise=rise))
    # Hedges either side of the Greenhouse Gate (its own short hedges continue them).
    for n, (x0, x1) in enumerate(HEDGE_RUNS):
        ko = _run_rect(x0, HEDGE_Z, 0.0, x1 - x0, 0.4)
        out.append(Placed(f"hedge_{n}", "kit_hedge_run", {"length": round(x1 - x0, 3), "height": 1.3, "depth": 0.75,
                                                          "seed": 11 + n},
                          (x0, gy, HEDGE_Z), 0.0, cell_of(g, (x0 + x1) / 2, HEDGE_Z), ko, ko))
    # The paddock's fence either side of its gate (the gate model carries 3 m either side).
    for n, (x0, x1) in enumerate(FENCE_RUNS):
        for seg, (x, xe, y, rise) in enumerate(_along(g, x0, x1, FENCE_Z, FENCE_SEG)):
            ko = _run_rect(x, FENCE_Z, 0.0, xe - x, 0.1)
            out.append(Placed(f"fence_{n}_{seg}", "kit_fence_rail", {"length": round(xe - x, 3), "height": 1.2,
                                                                      "rails": 3, "pitch": 2.2},
                              (round(x, 3), y, FENCE_Z), 0.0, cell_of(g, (x + xe) / 2, FENCE_Z), ko, None, rise))
    # Lamp posts.
    for pid, x, z in LAMPS:
        ko = _ko(x, z, 0.35, 0.35)
        out.append(Placed(f"lamp_{pid}", "kit_lamp_post", {"height": 4.0, "light": 1}, (x, _ground(g, x, z), z), 0.0,
                          cell_of(g, x, z), ko, ko))
    return out


# -- the foliage -------------------------------------------------------------------------------

# Copses on walkable ground: (centre x, z, radius, count, shapes, scale range, seed).
COPSES = (
    (-84.0, 124.0, 13.0, 7, ("tree_pine", "tree_round"), (0.9, 1.15), 1),  # round the Owl Loft's knoll
    (-62.0, 150.0, 9.0, 5, ("tree_round", "tree_broad"), (0.9, 1.2), 2),
    (-82.0, 176.0, 8.0, 4, ("tree_round", "tree_pine"), (0.95, 1.2), 3),
    (-8.0, 166.0, 6.0, 3, ("tree_broad", "tree_round"), (0.9, 1.1), 4),
    (102.0, 146.0, 9.0, 5, ("tree_round", "tree_broad", "tree_pine"), (0.9, 1.2), 5),
    (110.0, 186.0, 5.0, 3, ("tree_pine", "tree_round"), (0.95, 1.2), 6),
    (72.0, 100.0, 6.0, 3, ("tree_broad", "tree_round"), (0.9, 1.1), 7),
    (-62.0, 102.0, 8.0, 4, ("tree_round", "tree_broad"), (0.9, 1.15), 8),
    (28.0, 152.0, 8.0, 4, ("tree_broad", "tree_round"), (0.9, 1.2), 9),
    (98.0, 222.0, 8.0, 3, ("tree_round", "tree_pine"), (0.9, 1.1), 10),
    (-34.0, 140.0, 7.0, 3, ("tree_round", "tree_broad"), (0.95, 1.15), 11),
    (62.0, 170.0, 6.0, 3, ("tree_pine", "tree_round"), (0.9, 1.1), 12),
)
OLD_OAK = (-37.0, 121.5, 1.35)  # the old oak of the find spot `find_grounds_old_oak`
# Rocks: (centre x, z, radius, count, size range, seed).
ROCK_GROUPS = (
    (-84.0, 124.0, 11.0, 6, (0.7, 1.4), 21),  # the knoll's flanks
    (38.0, 254.0, 8.5, 4, (0.8, 1.6), 22),  # the find spot's rocks on the shore
    (-30.0, 247.0, 10.0, 4, (0.6, 1.5), 23),  # the beach
    (70.0, 252.0, 12.0, 5, (0.7, 1.8), 24),
    (-60.0, 230.0, 8.0, 3, (0.8, 1.6), 25),
    (110.0, 128.0, 5.0, 2, (0.8, 1.3), 26),
)
CRAG_ROCKS = (-112.0, -104.6, 94.0, 186.0, 16, 27)  # the crag strip (not walkable): x0, x1, z0, z1, count, seed
FOREST_ROWS = ((118.0, 4.6, 0.0), (121.6, 5.0, 2.1), (125.2, 5.4, 1.0))  # x, z step, z phase: the forest edge
FOREST_Z = (93.0, 244.0)


def _tries(seed: int, n: int):
    """Deterministic points in the unit disc (a golden-angle spiral, jittered)."""
    for i in range(n):
        r = math.sqrt((i + 0.5 + 0.4 * (_rand(seed, 2 * i) - 0.5)) / n)
        a = i * 2.399963 + 0.5 * _rand(seed, 2 * i + 1) + seed
        yield r * math.cos(a), r * math.sin(a)


def foliage(ctx: Context) -> list[Instance]:
    g = ctx.g
    out: list[Instance] = []

    def add(shape: str, x: float, z: float, scale: float, seed: int, sink: float) -> bool:
        half = (TRUNK.get(shape, 0.0) if shape != "rock" else 0.55 * SHAPES["rock"][1]["size"]) * scale
        if not free_spot(ctx, x, z, half):
            return False
        walk = g.walkable(x, z)
        h = g.height(x, z)
        if shape != "rock" and h < g.water_y + 0.4:
            return False  # trees keep out of the water
        if walk and shape != "rock" and CROWN_LOW[shape] * scale < 2.4:
            return False
        yaw = round(2 * math.pi * _rand(seed, 99), 4)
        ko = _ko(x, z, half, half) if walk else None
        n = sum(1 for i in out if i.shape == shape)
        out.append(Instance(f"{shape}_{n}", shape, (round(x, 3), round(h - sink * scale, 3), round(z, 3)), yaw,
                            round(scale, 3), ko))
        return True

    add("tree_broad", OLD_OAK[0], OLD_OAK[1], OLD_OAK[2], 500, 0.1)
    for cx, cz, rad, count, shapes, (s0, s1), seed in COPSES:
        placed = 0
        for k, (u, v) in enumerate(_tries(seed, count * 4)):
            if placed >= count:
                break
            shape = shapes[k % len(shapes)]
            scale = s0 + (s1 - s0) * _rand(seed, 50 + k)
            if add(shape, cx + rad * u, cz + rad * v, scale, seed * 100 + k, 0.1):
                placed += 1
        if placed < count:
            ctx.notes.append(f"copse at ({cx}, {cz}): {placed} of {count} trees fit")
    for cx, cz, rad, count, (s0, s1), seed in ROCK_GROUPS:
        placed = 0
        for k, (u, v) in enumerate(_tries(seed, count * 5)):
            if placed >= count:
                break
            size = s0 + (s1 - s0) * _rand(seed, 50 + k)
            if add("rock", cx + rad * u, cz + rad * v, size / SHAPES["rock"][1]["size"], seed * 100 + k, 0.12):
                placed += 1
        if placed < count:
            ctx.notes.append(f"rocks at ({cx}, {cz}): {placed} of {count} fit")
    x0, x1, z0, z1, count, seed = CRAG_ROCKS
    for k in range(count):
        x = x0 + (x1 - x0) * _rand(seed, 2 * k)
        z = z0 + (z1 - z0) * (k + _rand(seed, 2 * k + 1)) / count
        add("rock", x, z, 1.2 + 1.6 * _rand(seed, 70 + k), seed * 100 + k, 0.15)
    # The deep forest's first rows: pines and broad trees, nobody walks among them.
    for row, (x, dz, phase) in enumerate(FOREST_ROWS):
        z = FOREST_Z[0] + phase
        k = 0
        while z <= FOREST_Z[1]:
            jx = x + 1.4 * (_rand(40 + row, k) - 0.5)
            shape = "tree_broad" if _rand(60 + row, k) < 0.22 else "tree_pine"
            scale = (1.0 + 0.45 * _rand(50 + row, k)) * (1.15 if shape == "tree_pine" else 1.0)
            add(shape, jx, z + 1.2 * (_rand(45 + row, k) - 0.5), scale, 1000 * (row + 1) + k, 0.1)
            z += dz
            k += 1
    # A few pines on the crag strip's upper slope.
    for k in range(8):
        x = -111.0 + 4.5 * _rand(70, k)
        z = 96.0 + 9.5 * k + 3.0 * _rand(71, k)
        add("tree_pine", x, z, 0.9 + 0.4 * _rand(72, k), 7000 + k, 0.1)
    return out


# -- keep-outs ---------------------------------------------------------------------------------


def keep_outs(ctx: Context, placed: list[Placed], inst: list[Instance]) -> list[dict]:
    """The grounds_* keep-out entries: one per obstacle on walkable ground, cut at
    the grounds cells' borders (a keep-out holds on its `cell`'s surfaces only)."""
    g = ctx.g
    n = g.cell_size
    out = []
    rects = [(p.id, p.keep_out) for p in placed if p.keep_out is not None]
    rects += [(i.id, i.keep_out) for i in inst if i.keep_out is not None]
    for oid, (x0, z0, x1, z1) in rects:
        cuts = []
        for cx0 in sorted({g.x0 + n * math.floor((x0 - g.x0) / n), g.x0 + n * math.floor((x1 - g.x0) / n)}):
            for cz0 in sorted({g.z0 + n * math.floor((z0 - g.z0) / n), g.z0 + n * math.floor((z1 - g.z0) / n)}):
                a0, b0, a1, b1 = max(x0, cx0), max(z0, cz0), min(x1, cx0 + n), min(z1, cz0 + n)
                if a1 - a0 > 1e-6 and b1 - b0 > 1e-6:
                    cuts.append((a0, b0, a1, b1))
        for k, (a0, b0, a1, b1) in enumerate(cuts):
            out.append({"id": f"{KEEP_OUT_PREFIX}{oid}" + (f"_{k}" if len(cuts) > 1 else ""),
                        "min": [round(a0, 2), round(b0, 2)], "max": [round(a1, 2), round(b1, 2)],
                        "cell": cell_of(g, (a0 + a1) / 2, (b0 + b1) / 2)})
    return out


def keep_out_line(e: dict) -> str:
    def n(v):
        s = f"{v:.2f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s

    return (f'      {{ "id": "{e["id"]}", "min": [{n(e["min"][0])}, {n(e["min"][1])}], '
            f'"max": [{n(e["max"][0])}, {n(e["max"][1])}], "cell": "{e["cell"]}" }}')


def keep_out_text(text: str, entries: list[dict]) -> str:
    """keep_out.json with its grounds_* lines replaced by `entries` (the other lines untouched)."""
    lines = text.rstrip("\n").split("\n")
    kept = [ln for ln in lines if f'"id": "{KEEP_OUT_PREFIX}' not in ln]
    end = max(i for i, ln in enumerate(kept) if ln.strip() == "]")
    body = [ln.rstrip(",") for ln in kept[:end] if ln.strip().startswith("{ \"id\"")]
    head = [ln for ln in kept[:end] if not ln.strip().startswith("{ \"id\"")]
    rows = body + [keep_out_line(e) for e in entries]
    rows = [r + ("," if i < len(rows) - 1 else "") for i, r in enumerate(rows)]
    return "\n".join(head + rows + kept[end:]) + "\n"


def build(terrain_dir: str = TERRAIN_DIR, layout_path: str = LAYOUT_PATH):
    """(grounds, context, placements, foliage, keep-outs) from the data on disk."""
    layout = L.load_layout(layout_path)
    g = GT.Grounds.load(terrain_dir, layout)
    ctx = context(g, layout)
    placed = placements(ctx)
    inst = foliage(ctx)
    return g, ctx, placed, inst, keep_outs(ctx, placed, inst)


def occluders(placed: list[Placed], inst: list[Instance]) -> list[GT.Occluder]:
    out = [GT.Occluder(*p.occluder) for p in placed if p.occluder is not None]
    for i in inst:
        x, _, z = i.pos
        r = (TRUNK.get(i.shape) or 0.6) * i.scale
        out.append(GT.Occluder(x - r, z - r, x + r, z + r, reach=1.6 if i.shape != "rock" else 1.0,
                               depth=0.22 if i.shape != "rock" else 0.18))
    return out


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    g, ctx, placed, inst, kos = build()
    with open(KEEP_OUT_PATH, encoding="utf-8") as f:
        text = f.read()
    new = keep_out_text(text, kos)
    by = {}
    for i in inst:
        by[i.shape] = by.get(i.shape, 0) + 1
    print(f"grounds dressing: {len(placed)} pieces, {len(inst)} instances {by}, {len(kos)} keep-outs")
    for n in ctx.notes:
        print(f"  note: {n}")
    if args.check:
        sys.exit(0 if new == text else 1)
    if args.write and new != text:
        with open(KEEP_OUT_PATH, "w", encoding="utf-8") as f:
            f.write(new)
        print(f"wrote {KEEP_OUT_PATH}")


if __name__ == "__main__":
    main()
