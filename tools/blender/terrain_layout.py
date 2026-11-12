"""The grounds' heights and walkable rectangles, from a few control points.

    python3 -I tools/blender/terrain_layout.py            # writes the data
    python3 -I tools/blender/terrain_layout.py --check    # exits 1 if the data differs

Pure Python (no `bpy`), deterministic. One source for every consumer of the
grounds' shape (docs/architecture.md, docs/art/castle-kit.md "Exterior"):

* `data/world/terrain/<cell>.json`: each grounds cell's heights on the shared
  2 m lattice (the whole 40 x 40 m cell: lake bed, the crag strip and the
  forest strip included). The grounds export makes the terrain mesh from
  these, so feet stand on the drawn ground to 2 cm.
* `data/world/terrain/grounds.json`: the box, the lattice, the water level,
  the shoreline, the gravel paths, the building sites, the reserved sites
  (pitch, Owl Loft and bridge landing) and the cells.
* `data/world/layout.json`: the grounds' walkable rectangles (`kind`
  `grounds`), each with its `heights` (the same lattice values over its
  rectangle), the grounds landmarks, the class gates, the grounds find
  spots, and the Flying Pitch's surfaces (`cell` `pitch`),
  its landmark and class gate, and the generated areas: the pitch's `solid`
  stand bases and towers, the `airspace` boxes over the grounds and the lake
  and the `no_fly` boxes round the castle, the Owl Loft site, the buildings
  and the pitch's stands and towers. Only those entries are rewritten;
  everything else in the file is left as it is.

The grounds run south of the courtyard gate: x -112..128, z 92..292 (240 x
200 m, 6 x 5 cells of 40 m, `grounds_r<row>_c<col>`). The lake's water is at
`WATER_Y`; a walkable rectangle stops where the ground is more than `WADE` below
it (the shallows: ankle-deep, never swimming). The crag strip (x < -104) and
the forest strip (x > 116) are drawn ground nobody walks on.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
LAYOUT_PATH = os.path.join(ROOT, "data", "world", "layout.json")
PROPS_PATH = os.path.join(ROOT, "data", "world", "props.json")
TERRAIN_DIR = os.path.join(ROOT, "data", "world", "terrain")

VERSION = 1
STEP = 2.0
X0, X1 = -112.0, 128.0
Z0, Z1 = 92.0, 292.0
CELL = 40.0
COLS = int((X1 - X0) / CELL)  # 6
ROWS = int((Z1 - Z0) / CELL)  # 5
NX = int((X1 - X0) / STEP) + 1  # lattice points across
NZ = int((Z1 - Z0) / STEP) + 1

# The lake. Water is the absence of ground: a walkable rectangle covers only
# lattice squares whose four corners stand at least WATER_Y - WADE.
WATER_Y = -15.0
WADE = 0.3
# Walkable west of the forest's edge and east of the castle crag.
WEST_X = -104.0
CRAG_TAPER = 24.0  # metres south of the box's north edge over which the crag rises from the ground
FOREST_X = 116.0
# sim_core::layout::MAX_GROUND_SLOPE (rise over run between lattice points on
# a walkable rectangle; about 30 degrees).
MAX_SLOPE = 0.58

# The shoreline, west to east (x, z); land lies north of it.
SHORE = [
    (-160.0, 176.0), (-118.0, 186.0), (-100.0, 200.0), (-84.0, 216.0), (-56.0, 236.0),
    (-16.0, 250.0), (30.0, 256.0), (76.0, 252.0), (106.0, 242.0), (140.0, 228.0),
]
BEACH = 0.12  # the beach's rise per metre from the waterline
BEACH_BLEND = 16.0  # metres over which the beach meets the land
SHORE_RISE = 0.30  # the land never rises faster than this from the waterline

# Gaussian hills and hollows: (x, z, amplitude m, radius m).
HILLS = [
    (-84.0, 124.0, 5.0, 22.0),  # the Owl Loft's knoll
    (104.0, 112.0, 3.2, 20.0),  # the rise of the stone circle
    (40.0, 148.0, 2.0, 18.0),  # between the greenhouses and the hut
    (-30.0, 140.0, -2.0, 18.0),  # a hollow below the gate
    (-62.0, 176.0, 1.6, 14.0),  # a mound above the boathouse
    (76.0, 208.0, 1.8, 14.0),  # a hillock by the hut's field
    (14.0, 236.0, 0.9, 12.0),  # dunes behind the beach
]

# Flattened sites: (id, shape, target y or None for the height at its centre,
# falloff m). Shapes: ("rect", x0, z0, x1, z1) or ("circle", x, z, r).
FLAT = [
    ("gate_apron", ("rect", 8.0, 92.0, 24.0, 100.0), 0.0, 14.0),
    ("greenhouses", ("rect", 28.0, 96.0, 56.0, 116.0), None, 8.0),
    ("stone_circle", ("circle", 104.0, 112.0, 11.0), None, 10.0),
    ("bridge_landing", ("rect", 98.0, 92.0, 110.0, 104.0), "stone_circle", 6.0),
    ("owl_loft_knoll", ("circle", -84.0, 124.0, 8.0), None, 18.0),
    ("groundskeepers_cottage", ("circle", 100.0, 177.0, 10.0), None, 8.0),
    ("pitch", ("rect", -45.0, 176.0, 65.0, 224.0), -9.5, 18.0),
]

# Buildings on the grounds (outside only), on the lattice: holes in the
# walkable rectangles, so their walls stop a walker with no `solid` area (no
# per-query cost). (id, name, min x, min z, max x, max z, height above the
# lowest ground: the most the drawn building may rise).
BUILDINGS = [
    ("groundskeepers_cottage", "Groundskeeper's Cottage", 94.0, 172.0, 104.0, 182.0, 8.0),
    ("greenhouses", "Greenhouses", 30.0, 100.0, 54.0, 114.0, 7.0),
    ("boathouse", "Boathouse", -104.0, 196.0, -94.0, 210.0, 9.0),
]

# Sites kept clear for later milestones (no tree, rock, hedge or building).
RESERVED = [
    {"id": "pitch", "for": "3b: the Flying Pitch (about 100 x 40 m and its stands)",
     "min": [-45.0, 176.0], "max": [65.0, 224.0]},
    {"id": "owl_loft", "for": "3c: the Owl Loft tower on its knoll", "centre": [-84.0, 124.0], "radius": 8.0},
    {"id": "bridge_landing", "for": "3c: the covered wooden bridge lands here, at the stone circle's north side",
     "min": [98.0, 92.0], "max": [110.0, 104.0]},
]

# Gravel paths, painted on the terrain (width 3 m); polylines of (x, z).
PATHS = {
    "hut": [(16, 92), (18, 108), (40, 128), (66, 150), (86, 170), (92, 177)],
    "greenhouses": [(18, 100), (30, 98), (42, 97)],
    "stone_circle": [(40, 128), (66, 118), (93, 112)],
    "owl_loft": [(15, 104), (-20, 112), (-56, 120), (-76, 124)],
    "lake": [(16, 104), (0, 140), (-52, 172), (-52, 228), (-30, 244), (5, 251)],
    "boathouse": [(-52, 200), (-74, 202), (-91, 202)],
    "paddock": [(86, 170), (96, 184), (100, 188)],
}
PATH_WIDTH = 3.0

# Points placed on the ground: (section, id, x, z, extra fields).
LANDMARKS = [
    ("greenhouses", "Greenhouses", 42.0, 99.5),
    ("flying_pitch", "Flying Pitch", 10.0, 200.0),  # PITCH_LANDMARK
    ("paddock", "Creature Paddock", 100.0, 190.5),
    ("lake_shore", "Lake Shore", 5.0, 251.0),
    ("groundskeepers_cottage", "Groundskeeper's Cottage", 92.5, 177.0),
    ("boathouse", "Boathouse", -92.0, 202.0),
    ("stone_circle", "Stone Circle", 104.0, 112.0),
    ("owl_loft_knoll", "Owl Loft Knoll", -84.0, 124.0),
]
INTERACTABLES = {
    # id: (x, z)
    "greenhouse_gate": (42.0, 98.0),
    "paddock_gate": (100.0, 189.0),
    "find_grounds_old_oak": (-40.0, 116.0),
    "find_grounds_hut_path": (70.0, 150.0),
    "find_lake_shore_rocks": (38.0, 254.0),
}
# Kit models on the grounds (props.json): id -> (x, z); their cell follows.
KIT = {
    "gate_greenhouses": (42.0, 99.5),
    "gate_paddock": (100.0, 190.5),
}

# ---- The Flying Pitch and the airspace (docs/architecture.md).
# The pitch stands on the flattened terrace (FLAT "pitch", every lattice point of it at
# PITCH_Y). The field is the terrace's own grounds ground (the walkable rectangles are
# unchanged; the ones whose middle lies on the pitch are named "Flying Pitch"); the
# stands are raised decks (`cell` PITCH_CELL) reached by a flight of steps at each
# end; `solid` boxes under the decks and round the towers keep walkers out of them.
PITCH_CELL = "pitch"
PITCH_NAME = "Flying Pitch"
PITCH_Y = -9.5
PITCH_SITE = (-45.0, 176.0, 65.0, 224.0)  # the reserved terrace (x0, z0, x1, z1)
PITCH_FIELD = (-38.0, 181.5, 58.0, 218.5)  # the drawn oval's box: 96 x 37 m
PITCH_CENTRE = (10.0, 200.0)
STAND_RISE = 2.4  # a deck stands this far over the field
STEPS_RUN = 4.0  # its steps climb STAND_RISE over this run (slope 0.6)
STEPS_FOOT = 0.55  # the steps' foot: where they rise less than a step (0.35 m) over the field
SOLID_PAD = 0.05
# (id, x0, z0, x1, z1, yaw the seats face: the field): the decks; their steps run east of x1
# (`<id>_steps`) and west of x0 (`<id>_steps_w`).
PITCH_STANDS = [
    ("pitch_stand_n", -25.0, 176.5, 41.0, 181.5, 0.0),
    ("pitch_stand_s", -25.0, 218.5, 41.0, 223.5, 3.1415927),
]
# (id, x, z, house slot): spectator towers draped in house colours (house_a..d).
PITCH_TOWERS = [
    ("pitch_tower_a", -41.5, 189.0, "house_a"),
    ("pitch_tower_b", -41.5, 211.0, "house_b"),
    ("pitch_tower_c", 61.5, 189.0, "house_c"),
    ("pitch_tower_d", 61.5, 211.0, "house_d"),
]
TOWER_SIZE = 4.0  # square footprint (m)
TOWER_TOP = 20.0  # metres over the field (the drapes hang below the viewing box)
STAND_TOP = 3.8  # a stand's roof over its deck (m)
# (id, x, z, ring centre over the field, ring radius): three hoops at each end.
HOOP_RADIUS = 1.3
PITCH_HOOPS = [
    ("hoop_w_1", -32.0, 194.0, 13.5), ("hoop_w_2", -32.0, 200.0, 16.5), ("hoop_w_3", -32.0, 206.0, 13.5),
    ("hoop_e_1", 52.0, 194.0, 13.5), ("hoop_e_2", 52.0, 200.0, 16.5), ("hoop_e_3", 52.0, 206.0, 13.5),
]
# The Flying class's gate (a `class_door`, data/classes/schedule.json `flying`) on the
# field's north-west corner, facing the castle path; the pitch's landmark mid-field.
PITCH_GATE = {"id": "pitch_gate", "name": "Pitch Gate", "pos": (-34.0, 178.5), "prop": (-34.0, 177.0)}

# Airspace (sim_core::flight): boxes where a mounted broom may fly, up to the ceiling
# (data/flying.json `ceiling_y`: about 60 m over the lake, 55 m over the pitch). The
# floor is the ground under a flyer or, where there is none (the water), the box's
# bottom: the wading limit, so the lowest a broom flies there (`ground_clearance` 0.3 m
# over it) is the water's surface. Boxes that meet overlap by 2 m (a flyer keeps a
# margin inside them).
CEILING_Y = 45.0
AIR_FLOOR = WATER_Y - WADE
AIRSPACE = [
    ("air_grounds", "Over the grounds", (WEST_X, AIR_FLOOR, Z0), (FOREST_X, CEILING_Y, Z1)),
    ("air_lake_south", "Over the lake", (-200.0, AIR_FLOOR, Z1 - 2.0), (150.0, CEILING_Y, 420.0)),
    ("air_lake_west", "Over the lake by the boathouse", (-200.0, AIR_FLOOR, 210.0), (WEST_X + 2.0, CEILING_Y, Z1)),
]
SKY_TOP = 200.0  # a no-fly box that reaches this high closes the sky above it too
# Boxes no broom enters: (id, name, min, max). The castle (everything north of the
# grounds' first 8 m), the Owl Loft's knoll (tower) and the Groundskeeper's Cottage to the sky; the
# greenhouses and the boathouse to 2 m over their roofs; the pitch's stands and towers.
NO_FLY_CASTLE = ("nofly_castle", "The castle", (-150.0, -60.0, -140.0), (150.0, SKY_TOP, 100.0))
NO_FLY_OWL_LOFT = ("nofly_owl_loft", "The Owl Loft", (-96.0, -20.0, 112.0), (-72.0, SKY_TOP, 136.0))
NO_FLY_PAD = 1.0  # round a building's footprint
NO_FLY_ROOF = 2.0  # over a low building's roof
NO_FLY_TO_SKY = ("groundskeepers_cottage",)


# --------------------------------------------------------------- heights


def smoothstep(e0: float, e1: float, v: float) -> float:
    t = min(1.0, max(0.0, (v - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def shore_distance(x: float, z: float) -> float:
    """Signed distance to the shoreline: positive on land (north of it)."""
    best = (math.inf, 1.0)
    for (ax, az), (bx, bz) in zip(SHORE, SHORE[1:]):
        dx, dz = bx - ax, bz - az
        t = max(0.0, min(1.0, ((x - ax) * dx + (z - az) * dz) / (dx * dx + dz * dz)))
        px, pz = ax + dx * t, az + dz * t
        d = math.hypot(x - px, z - pz)
        if d < best[0]:
            cross = dx * (z - az) - dz * (x - ax)
            best = (d, -1.0 if cross > 0 else 1.0)
    return best[0] * best[1]


def land(x: float, z: float) -> float:
    """The hill from the castle down toward the lake, before the shore and the sites."""
    t = min(1.0, max(0.0, (z - 96.0) / 150.0))
    h = -13.0 * t * t * (3.0 - 2.0 * t)
    for cx, cz, amp, r in HILLS:
        h += amp * math.exp(-((x - cx) ** 2 + (z - cz) ** 2) / (2.0 * r * r))
    h += 0.8 * math.sin(x / 23.0) * math.cos(z / 19.0)
    # The castle crag west of the walkable ground, and a bank before the forest. The crag
    # eases down to the ground's height toward the castle side (CRAG_TAPER): it used to
    # stand 10 m tall at the box's north edge and end there in a sheer cut short of the
    # great west tower, the lake showing under it ("Grounds edges", docs/architecture.md).
    h += min(10.0, max(0.0, WEST_X - x) * 1.4) * smoothstep(Z0, Z0 + CRAG_TAPER, z)
    h += min(3.0, max(0.0, x - FOREST_X) * 0.25)
    return h


def shaped(x: float, z: float) -> float:
    d = shore_distance(x, z)
    beach = WATER_Y + BEACH * d
    if d <= 0.0:
        return max(WATER_Y - 4.0, beach)
    w = smoothstep(0.0, BEACH_BLEND, d)
    cap = WATER_Y + WADE + SHORE_RISE * d
    lnd = land(x, z)
    # A smooth minimum: the land eases under the cap instead of creasing.
    k = 3.0
    hmin = max(k - abs(lnd - cap), 0.0) / k
    lnd = min(lnd, cap) - hmin * hmin * k * 0.25
    return beach * (1.0 - w) + lnd * w


def _site_weight(shape, x: float, z: float, fall: float) -> float:
    if shape[0] == "rect":
        _, x0, z0, x1, z1 = shape
        dx = max(x0 - x, 0.0, x - x1)
        dz = max(z0 - z, 0.0, z - z1)
        dist = math.hypot(dx, dz)
    else:
        _, cx, cz, r = shape
        dist = max(0.0, math.hypot(x - cx, z - cz) - r)
    return 1.0 - smoothstep(0.0, fall, dist)


def _centre(shape) -> tuple[float, float]:
    if shape[0] == "rect":
        return (shape[1] + shape[3]) / 2.0, (shape[2] + shape[4]) / 2.0
    return shape[1], shape[2]


def site_targets() -> dict[str, float]:
    out: dict[str, float] = {}
    for sid, shape, target, _ in FLAT:
        if target is None:
            out[sid] = round(shaped(*_centre(shape)), 2)
        elif isinstance(target, str):
            out[sid] = out[target]
        else:
            out[sid] = float(target)
    return out


def height(x: float, z: float, targets: dict[str, float]) -> float:
    h = shaped(x, z)
    for sid, shape, _, fall in FLAT:
        w = _site_weight(shape, x, z, fall)
        if w > 0.0:
            h = h * (1.0 - w) + targets[sid] * w
    return h


def lattice() -> list[list[float]]:
    """Heights on the 2 m lattice, rows of z, each a row of x, rounded to 1 cm."""
    targets = site_targets()
    return [[round(height(X0 + i * STEP, Z0 + j * STEP, targets), 2) + 0.0 for i in range(NX)] for j in range(NZ)]


def bilinear(grid, x: float, z: float) -> float:
    """sim_core's heightfield rule (`Heights::height_at`) on the whole lattice."""
    fx = min(max((x - X0) / STEP, 0.0), NX - 1.0)
    fz = min(max((z - Z0) / STEP, 0.0), NZ - 1.0)
    i, j = min(int(fx), NX - 2), min(int(fz), NZ - 2)
    tx, tz = fx - i, fz - j
    a = grid[j][i] + (grid[j][i + 1] - grid[j][i]) * tx
    b = grid[j + 1][i] + (grid[j + 1][i + 1] - grid[j + 1][i]) * tx
    return a + (b - a) * tz


# --------------------------------------------------------------- walkable


def square_walkable(grid, i: int, j: int) -> bool:
    """Lattice square (i, j) (x from X0 + i*STEP, z from Z0 + j*STEP)."""
    x0, x1 = X0 + i * STEP, X0 + (i + 1) * STEP
    z0, z1 = Z0 + j * STEP, Z0 + (j + 1) * STEP
    if x0 < WEST_X - 1e-6 or x1 > FOREST_X + 1e-6:
        return False
    for _, _, bx0, bz0, bx1, bz1, _ in BUILDINGS:
        if x0 < bx1 - 1e-6 and x1 > bx0 + 1e-6 and z0 < bz1 - 1e-6 and z1 > bz0 + 1e-6:
            return False
    corners = (grid[j][i], grid[j][i + 1], grid[j + 1][i], grid[j + 1][i + 1])
    return min(corners) >= WATER_Y - WADE - 1e-9


def cell_id(r: int, c: int) -> str:
    return f"grounds_r{r}_c{c}"


def cell_rects(grid, r: int, c: int) -> list[tuple[int, int, int, int]]:
    """The cell's walkable squares as rectangles (i0, j0, i1, j1 exclusive),
    greedily largest first (ties: north-west first)."""
    n = int(CELL / STEP)
    ib, jb = c * n, r * n
    free = [[square_walkable(grid, ib + a, jb + b) for a in range(n)] for b in range(n)]
    rects = []
    while True:
        # The largest all-free rectangle: per row, the column heights of free
        # squares ending there, and the largest rectangle under them.
        best = None
        heights = [0] * n
        for b in range(n):
            for a in range(n):
                heights[a] = heights[a] + 1 if free[b][a] else 0
            for a in range(n):
                h = heights[a]
                if h == 0:
                    continue
                lo = a
                while lo > 0 and heights[lo - 1] >= h:
                    lo -= 1
                hi = a
                while hi + 1 < n and heights[hi + 1] >= h:
                    hi += 1
                area = (hi - lo + 1) * h
                key = (area, -(b - h + 1), -lo)
                if best is None or key > best[0]:
                    best = (key, lo, b - h + 1, hi + 1, b + 1)
        if best is None:
            break
        _, a0, b0, a1, b1 = best
        for b in range(b0, b1):
            for a in range(a0, a1):
                free[b][a] = False
        rects.append((ib + a0, jb + b0, ib + a1, jb + b1))
    return rects


def fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def num(v: float) -> str:
    return fmt(round(v, 3)) if abs(v - round(v, 2)) > 1e-9 else fmt(v)


def surfaces(grid) -> list[dict]:
    out = []
    for r in range(ROWS):
        for c in range(COLS):
            rects = cell_rects(grid, r, c)
            for k, (i0, j0, i1, j1) in enumerate(rects):
                h = [grid[j][i] for j in range(j0, j1 + 1) for i in range(i0, i1 + 1)]
                x0, z0 = X0 + i0 * STEP, Z0 + j0 * STEP
                x1, z1 = X0 + i1 * STEP, Z0 + j1 * STEP
                # Named by the rectangle's middle: a big rectangle that only
                # reaches the beach at one corner (Groundskeeper's Cottage, the paddock
                # gate) is still the Castle Grounds in the top bar.
                mx, mz = (x0 + x1) / 2, (z0 + z1) / 2
                near_shore = shore_distance(mx, mz) < 30.0
                px0, pz0, px1, pz1 = PITCH_SITE
                on_pitch = px0 <= mx <= px1 and pz0 <= mz <= pz1
                name = PITCH_NAME if on_pitch else "Lake Shore" if near_shore else "Castle Grounds"
                out.append({
                    "id": cell_id(r, c) if k == 0 else f"{cell_id(r, c)}_{k}",
                    "name": name,
                    "cell": cell_id(r, c),
                    "min": [x0, z0], "max": [x1, z1], "y": min(h),
                    "heights": {"origin": [x0, z0], "step": STEP, "cols": i1 - i0 + 1, "rows": j1 - j0 + 1, "h": h},
                })
    return out


def surface_lines(s: dict) -> list[str]:
    hf = s["heights"]
    return [
        f'    {{ "id": "{s["id"]}", "kind": "grounds", "name": "{s["name"]}", "level": 0, "cell": "{s["cell"]}",',
        f'      "min": [{fmt(s["min"][0])}, {fmt(s["min"][1])}], "max": [{fmt(s["max"][0])}, {fmt(s["max"][1])}], "y": {fmt(s["y"])},',
        f'      "heights": {{ "origin": [{fmt(hf["origin"][0])}, {fmt(hf["origin"][1])}], "step": {fmt(hf["step"])}, '
        f'"cols": {hf["cols"]}, "rows": {hf["rows"]}, "h": [{", ".join(fmt(v) for v in hf["h"])}] }} }}',
    ]


def cell_of(x: float, z: float) -> str:
    c = min(COLS - 1, max(0, int((x - X0) // CELL)))
    r = min(ROWS - 1, max(0, int((z - Z0) // CELL)))
    return cell_id(r, c)


def ground(grid, x: float, z: float) -> float:
    return round(bilinear(grid, x, z), 2) + 0.0


def solids(grid) -> list[dict]:
    out = []
    for bid, name, x0, z0, x1, z1, tall in BUILDINGS:
        hs = [grid[j][i] for j in range(NZ) for i in range(NX)
              if x0 - 1e-6 <= X0 + i * STEP <= x1 + 1e-6 and z0 - 1e-6 <= Z0 + j * STEP <= z1 + 1e-6]
        lo, hi = math.floor(min(hs) - 1.0), math.ceil(max(hs) + tall)
        out.append({"id": bid, "name": name, "min": [x0, float(lo), z0], "max": [x1, float(hi), z1]})
    return out


# --------------------------------------------------------------- the pitch and the airspace


def pitch_surfaces() -> list[dict]:
    """The stands' decks and their steps, in the pitch's own cell."""
    out = []
    for sid, x0, z0, x1, z1, _ in PITCH_STANDS:
        deck = round(PITCH_Y + STAND_RISE, 2)
        out.append({"id": sid, "kind": "grounds", "name": PITCH_NAME, "cell": PITCH_CELL,
                    "min": [x0, z0], "max": [x1, z1], "y": deck})
        out.append({"id": f"{sid}_steps", "kind": "stair", "name": PITCH_NAME, "cell": PITCH_CELL,
                    "min": [x1, z0], "max": [x1 + STEPS_RUN, z1], "y": PITCH_Y, "y_top": deck, "rise": "-x"})
        out.append({"id": f"{sid}_steps_w", "kind": "stair", "name": PITCH_NAME, "cell": PITCH_CELL,
                    "min": [x0 - STEPS_RUN, z0], "max": [x0, z1], "y": PITCH_Y, "y_top": deck, "rise": "+x"})
    return out


def pitch_surface_line(s: dict) -> str:
    parts = [f'"id": "{s["id"]}"', f'"kind": "{s["kind"]}"', f'"name": "{s["name"]}"', '"level": 0',
             f'"cell": "{s["cell"]}"', f'"min": [{fmt(s["min"][0])}, {fmt(s["min"][1])}]',
             f'"max": [{fmt(s["max"][0])}, {fmt(s["max"][1])}]', f'"y": {fmt(s["y"])}']
    if "y_top" in s:
        parts += [f'"y_top": {fmt(s["y_top"])}', f'"rise": "{s["rise"]}"']
    return "    { " + ", ".join(parts) + " }"


def tower_box(x: float, z: float, pad: float = 0.0) -> tuple[float, float, float, float]:
    h = TOWER_SIZE / 2 + pad
    return x - h, z - h, x + h, z + h


def areas(grid) -> list[dict]:
    """The generated areas: the pitch's solids, the airspace, the no-fly boxes."""
    out = []
    # Walkers: the field under a deck and its steps' upper part, and the towers.
    for sid, x0, z0, x1, z1, _ in PITCH_STANDS:
        # The box stops STEPS_FOOT short of both flights' feet: the foot (where the steps
        # meet the field within a step's height) stays outside the box grown by a
        # walker's clearance, so the trail and the bots step on there.
        # It reaches SOLID_PAD past the deck's sides, so no field ground is left
        # on the deck's outline under it.
        p = SOLID_PAD
        out.append({"id": f"{sid}_base", "kind": "solid", "name": "Flying Pitch stand",
                    "min": [x0 - STEPS_RUN + STEPS_FOOT, PITCH_Y - 1.0, z0 - p],
                    "max": [x1 + STEPS_RUN - STEPS_FOOT, PITCH_Y + 0.25, z1 + p]})
    for tid, x, z, _ in PITCH_TOWERS:
        bx0, bz0, bx1, bz1 = tower_box(x, z)
        out.append({"id": tid, "kind": "solid", "name": "Flying Pitch tower",
                    "min": [bx0, PITCH_Y - 2.5, bz0], "max": [bx1, PITCH_Y + TOWER_TOP, bz1]})
    for aid, name, lo, hi in AIRSPACE:
        out.append({"id": aid, "kind": "airspace", "name": name, "min": list(lo), "max": list(hi)})
    for aid, name, lo, hi in (NO_FLY_CASTLE, NO_FLY_OWL_LOFT):
        out.append({"id": aid, "kind": "no_fly", "name": name, "min": list(lo), "max": list(hi)})
    for sol in solids(grid):
        x0, lo, z0 = sol["min"]
        x1, hi, z1 = sol["max"]
        top = SKY_TOP if sol["id"] in NO_FLY_TO_SKY else hi + NO_FLY_ROOF
        out.append({"id": f"nofly_{sol['id']}", "kind": "no_fly", "name": sol["name"],
                    "min": [x0 - NO_FLY_PAD, lo, z0 - NO_FLY_PAD], "max": [x1 + NO_FLY_PAD, top, z1 + NO_FLY_PAD]})
    for sid, x0, z0, x1, z1, _ in PITCH_STANDS:
        out.append({"id": f"nofly_{sid}", "kind": "no_fly", "name": "Flying Pitch stand",
                    "min": [x0 - STEPS_RUN - 0.5, PITCH_Y - 6.0, z0 - 0.5],
                    "max": [x1 + STEPS_RUN + 0.5, PITCH_Y + STAND_RISE + STAND_TOP + 1.0, z1 + 0.5]})
    for tid, x, z, _ in PITCH_TOWERS:
        bx0, bz0, bx1, bz1 = tower_box(x, z, 0.5)
        out.append({"id": f"nofly_{tid}", "kind": "no_fly", "name": "Flying Pitch tower",
                    "min": [bx0, PITCH_Y - 6.0, bz0], "max": [bx1, PITCH_Y + TOWER_TOP + 1.5, bz1]})
    return out


def area_line(a: dict) -> str:
    v = lambda xs: "[" + ", ".join(fmt(x) for x in xs) + "]"  # noqa: E731
    return (f'    {{ "id": "{a["id"]}", "kind": "{a["kind"]}", "name": "{a["name"]}", '
            f'"min": {v(a["min"])}, "max": {v(a["max"])} }}')


GENERATED_AREA = re.compile(r'^    \{ "id": "(air_|nofly_|pitch_)')


def pitch_meta() -> dict:
    """grounds.json `pitch`: what the pitch export draws where (docs/architecture.md)."""
    return {
        "cell": PITCH_CELL,
        "y": PITCH_Y,
        "site": list(PITCH_SITE),
        "field": list(PITCH_FIELD),
        "centre": list(PITCH_CENTRE),
        "stands": [{"id": sid, "deck": [x0, z0, x1, z1], "y": round(PITCH_Y + STAND_RISE, 2),
                    "steps": [[x1, z0, x1 + STEPS_RUN, z1], [x0 - STEPS_RUN, z0, x0, z1]], "seats_face": yaw, "roof": STAND_TOP}
                   for sid, x0, z0, x1, z1, yaw in PITCH_STANDS],
        "towers": [{"id": tid, "centre": [x, z], "size": TOWER_SIZE, "top": PITCH_Y + TOWER_TOP, "house": slot}
                   for tid, x, z, slot in PITCH_TOWERS],
        "hoops": [{"id": hid, "pos": [x, round(PITCH_Y + up, 2), z], "radius": HOOP_RADIUS, "yaw": 1.5707963}
                  for hid, x, z, up in PITCH_HOOPS],
        "gate": {"id": PITCH_GATE["id"], "pos": [PITCH_GATE["pos"][0], PITCH_Y, PITCH_GATE["pos"][1]],
                 "prop": [PITCH_GATE["prop"][0], PITCH_Y, PITCH_GATE["prop"][1]], "yaw": 3.1415927},
        "ceiling_y": CEILING_Y,
    }


# --------------------------------------------------------------- output


def terrain_files(grid) -> dict[str, str]:
    n = int(CELL / STEP)
    files = {}
    for r in range(ROWS):
        for c in range(COLS):
            i0, j0 = c * n, r * n
            rows = [", ".join(fmt(grid[j][i]) for i in range(i0, i0 + n + 1)) for j in range(j0, j0 + n + 1)]
            body = ",\n    ".join(rows)
            files[f"{cell_id(r, c)}.json"] = (
                "{\n"
                f'  "version": {VERSION},\n'
                f'  "cell": "{cell_id(r, c)}",\n'
                f'  "origin": [{fmt(X0 + i0 * STEP)}, {fmt(Z0 + j0 * STEP)}],\n'
                f'  "step": {fmt(STEP)},\n'
                f'  "cols": {n + 1},\n'
                f'  "rows": {n + 1},\n'
                '  "_doc": "Heights (m) on the grounds lattice, row-major: rows run south (+z) from origin z, each row east (+x) from origin x. Generated by tools/blender/terrain_layout.py; do not edit.",\n'
                f"  \"heights\": [\n    {body}\n  ]\n"
                "}\n"
            )
    return files


def meta(grid, surfs) -> str:
    targets = site_targets()
    data = {
        "_doc": "The grounds (docs/architecture.md, docs/art/castle-kit.md 'Exterior'). "
                "Generated by tools/blender/terrain_layout.py from its control points; do not edit. "
                "Heights per cell in <cell>.json on the shared lattice; the walkable rectangles are the layout's "
                "`grounds` surfaces (their `heights` are the same lattice values). Water is the absence of ground: "
                "walkable ground never lies more than `wade` below `water_y`.",
        "version": VERSION,
        "box": {"min": [X0, Z0], "max": [X1, Z1]},
        "step": STEP,
        "cell_size": CELL,
        "cells": [cell_id(r, c) for r in range(ROWS) for c in range(COLS)],
        "water_y": WATER_Y,
        "wade": WADE,
        "max_slope": MAX_SLOPE,
        "walkable_x": [WEST_X, FOREST_X],
        "shoreline": [list(p) for p in SHORE],
        "paths": {k: {"width": PATH_WIDTH, "points": [list(map(float, p)) for p in v]} for k, v in PATHS.items()},
        "sites": {sid: {"shape": list(shape), "y": targets[sid]} for sid, shape, _, _ in FLAT},
        "buildings": [{"id": sol["id"], "name": sol["name"], "min": [sol["min"][0], sol["min"][2]],
                       "max": [sol["max"][0], sol["max"][2]], "y": [sol["min"][1], sol["max"][1]]} for sol in solids(grid)],
        "reserved": RESERVED,
        "pitch": pitch_meta(),
        "height_range": [min(min(r) for r in grid), max(max(r) for r in grid)],
        "walkable_surfaces": len(surfs),
    }
    return json.dumps(data, indent=2) + "\n"


SURFACE_BLOCK_START = re.compile(r'^    \{ "id": "grounds_')


def patch_layout(text: str, grid, surfs) -> str:
    lines = text.split("\n")
    # 1. The grounds surfaces: the run of lines from the first grounds
    # surface to the end of the surfaces list.
    start = next(i for i, ln in enumerate(lines) if SURFACE_BLOCK_START.match(ln))
    end = next(i for i in range(start, len(lines)) if lines[i] == "  ],")
    block: list[str] = []
    for s in surfs:
        sl = surface_lines(s)
        sl[-1] += ","
        block += sl
    pitch = [pitch_surface_line(p) for p in pitch_surfaces()]
    block += [ln + "," for ln in pitch[:-1]] + pitch[-1:]
    if lines[start - 1].strip() and not lines[start - 1].rstrip().endswith(","):
        lines[start - 1] += ","
    lines[start:end] = block
    text = "\n".join(lines)

    def set_pos(text: str, eid: str, pos: list[float]) -> str:
        pat = re.compile(r'(\{ "id": "' + re.escape(eid) + r'",[^\n]*?"pos": )\[[^\]]*\]')
        new, n = pat.subn(lambda m: m.group(1) + "[" + ", ".join(num(v) for v in pos) + "]", text)
        if n != 1:
            raise SystemExit(f"terrain_layout: {eid!r} found {n} times in layout.json")
        return new

    def insert(text: str, section: str, eid: str, line: str) -> str:
        if f'"id": "{eid}"' in text:
            return text
        head = text.index(f'  "{section}": [')
        close = text.index("\n  ]", head)
        return text[:close] + ",\n" + line + text[close:]

    for lid, name, x, z in LANDMARKS:
        pos = [x, ground(grid, x, z), z]
        line = f'    {{ "id": "{lid}", "name": "{name}", "pos": [{", ".join(num(v) for v in pos)}] }}'
        text = insert(text, "landmarks", lid, line)
        text = set_pos(text, lid, pos)
    for iid, (x, z) in INTERACTABLES.items():
        text = set_pos(text, iid, [x, ground(grid, x, z), z])
    # The Flying class's gate.
    gx, gz = PITCH_GATE["pos"]
    gate = [gx, ground(grid, gx, gz), gz]
    line = (f'    {{ "id": "{PITCH_GATE["id"]}", "kind": "class_door", "name": "{PITCH_GATE["name"]}", '
            f'"pos": [{", ".join(num(v) for v in gate)}], "yaw": 3.1415927, "radius": 1.8 }}')
    text = insert(text, "interactables", PITCH_GATE["id"], line)
    text = set_pos(text, PITCH_GATE["id"], gate)
    # The generated areas replace any earlier ones, after the hand-written ones.
    lines = text.split("\n")
    head = lines.index('  "areas": [')
    close = next(i for i in range(head, len(lines)) if lines[i] == "  ]")
    kept = [ln.rstrip(",") for ln in lines[head + 1:close] if not GENERATED_AREA.match(ln)]
    rows = kept + [area_line(a) for a in areas(grid)]
    lines[head + 1:close] = [ln + "," for ln in rows[:-1]] + rows[-1:]
    text = "\n".join(lines)
    # No solid areas for the buildings (they are holes): drop any left over.
    text = re.sub(r',\n    \{ "id": "[a-z_]+_building", "kind": "solid"[^\n]*\}', "", text)
    return text


def patch_props(text: str, grid) -> str:
    for kid, (x, z) in KIT.items():
        pat = re.compile(r'(\{ "id": "' + re.escape(kid) + r'", "cell": ")[a-z0-9_]+(", "model": "[a-z_]+", "pos": )\[[^\]]*\]')
        pos = "[" + ", ".join(num(v) for v in (x, ground(grid, x, z), z)) + "]"
        text, n = pat.subn(lambda m: m.group(1) + cell_of(x, z) + m.group(2) + pos, text)
        if n != 1:
            raise SystemExit(f"terrain_layout: kit entry {kid!r} found {n} times in props.json")
    return text


def outputs() -> dict[str, str]:
    grid = lattice()
    surfs = surfaces(grid)
    with open(LAYOUT_PATH, encoding="utf-8") as f:
        layout = f.read()
    with open(PROPS_PATH, encoding="utf-8") as f:
        props = f.read()
    out = {
        LAYOUT_PATH: patch_layout(layout, grid, surfs),
        PROPS_PATH: patch_props(props, grid),
        os.path.join(TERRAIN_DIR, "grounds.json"): meta(grid, surfs),
    }
    for name, body in terrain_files(grid).items():
        out[os.path.join(TERRAIN_DIR, name)] = body
    return out


def report(grid) -> list[str]:
    surfs = surfaces(grid)
    worst = 0.0
    for s in surfs:
        hf = s["heights"]
        c, r, h = hf["cols"], hf["rows"], hf["h"]
        for j in range(r):
            for i in range(c):
                if i + 1 < c:
                    worst = max(worst, abs(h[j * c + i + 1] - h[j * c + i]) / STEP)
                if j + 1 < r:
                    worst = max(worst, abs(h[(j + 1) * c + i] - h[j * c + i]) / STEP)
    walk = [v for s in surfs for v in s["heights"]["h"]]
    return [
        f"{len(surfs)} walkable rectangles in {ROWS * COLS} cells",
        f"lattice {NX} x {NZ}, heights {min(min(r) for r in grid):.2f}..{max(max(r) for r in grid):.2f}, "
        f"walkable {min(walk):.2f}..{max(walk):.2f}",
        f"steepest walkable slope {worst:.3f} ({math.degrees(math.atan(worst)):.1f} deg; cap {MAX_SLOPE})",
        "sites: " + ", ".join(f"{k} {v}" for k, v in site_targets().items()),
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if the committed data differs")
    args = ap.parse_args()
    outs = outputs()
    stale = []
    for path, body in outs.items():
        cur = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if cur != body:
            stale.append(os.path.relpath(path, ROOT))
            if not args.check:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(body)
    # Terrain files of cells that no longer exist.
    for name in sorted(os.listdir(TERRAIN_DIR)) if os.path.isdir(TERRAIN_DIR) else []:
        path = os.path.join(TERRAIN_DIR, name)
        if name.endswith(".json") and path not in outs:
            stale.append(os.path.relpath(path, ROOT))
            if not args.check:
                os.remove(path)
    for line in report(lattice()):
        print(f"terrain_layout: {line}")
    if args.check:
        if stale:
            print("terrain_layout: out of date: " + ", ".join(stale))
            sys.exit(1)
        print("terrain_layout: up to date")
    else:
        print(f"terrain_layout: wrote {len(stale)} file(s)")


if __name__ == "__main__":
    main()
