"""`class_divination`, the Divination classroom at the
top of its tower (docs/architecture.md).
Pure Python, no `bpy`.

A warm, close tower room built from castle-kit pieces and the M5b room
furniture set: the warm castle stone (`castle_wall_b`, `castle_floor_long`)
under a low barrel vault on three ribs, tall lancets in both side walls,
each between wine drapes (`kit_drapes` cloth 1: the room is no house's),
and the students' round tables (`kit_table_round`) set out in three
staggered rows on rugs, each with three or four wing armchairs
(`kit_armchair`, in rose cloth) round its far side facing the table and the
professor, and on each table a teacup per place or a small crystal ball per
place (`kit_divination_set`) round a teapot or a candle. The professor's
table before the north wall carries the large crystal ball; shelves of
jars and pots flank it and the door; a ring chandelier of candles hangs from
the vault's crown (still: an instance room has no moving nodes) with two
hanging lanterns over the side rows.

The kit walls are straight, so the room is the contract's 20 x 16 m floor;
its roundness is in the dressing (the tables' rings of armchairs, the ring
chandelier, the drapes all round). Contract ids and spots as every class
room: `class_floor`, `class_entry_a/b`, `class_exit` at the south wall's
class door, `class_professor` at [0, 0, -6]. Colours: castle_* materials and
props-atlas palette names only; light colours from `palette.py`. No colour
literal here."""

from __future__ import annotations

import math

import palette

PI = round(math.pi, 7)
HALF_PI = round(math.pi / 2, 4)

D_SPRING, D_RISE = 6.0, 2.4
D_RIBS = 4.0  # the ribs at z -4, 0, 4 on both side walls' shafts
TABLE_TOP = 0.74  # kit_table_round's top
CHAIR_R = 1.4  # an armchair's seat centre from its table's centre (the knee room: grid.KNEE_CLEAR_AHEAD past the top)
CHAIR_R4 = 1.5  # with four armchairs round a table
# The students' tables: (x, z, armchairs). Three staggered rows facing the
# professor; the armchairs on each table's far side (+z), facing the table.
TABLES = (
    (-6.9, -2.3, 3), (-2.3, -2.3, 3), (2.3, -2.3, 3), (6.9, -2.3, 3),
    (-4.6, 1.6, 4), (0.0, 1.6, 4), (4.6, 1.6, 4),
    (-6.6, 4.7, 4), (6.6, 4.7, 4),
)
SPREAD = {3: 66.0, 4: 58.0}  # degrees between a table's armchairs
PROFESSOR_TABLE = (0.0, -7.25)
# The armchairs in rose cloth (grid.FURNITURE_COVERS 5), not the leather
# (cover 4), which read almost black at Medium; the common rooms' armchairs keep theirs.
ARMCHAIR_COVER = 5


def chair_spots(x: float, z: float, n: int) -> list[tuple[float, float, float]]:
    """(x, z, yaw) of a table's n armchairs round its +z side, each facing the
    table's centre."""
    r = CHAIR_R if n <= 3 else CHAIR_R4
    out = []
    for i in range(n):
        a = math.radians(SPREAD[n] * (i - (n - 1) / 2.0))
        out.append((round(x + r * math.sin(a), 4), round(z + r * math.cos(a), 4), round(a + math.pi, 4)))
    return out


def _light(lid: str, pos, glow: str, intensity: float, rng: float) -> dict:
    return {"id": lid, "kind": "point", "pos": [float(v) for v in pos], "color": palette.GLOW[glow],
            "intensity": intensity, "range": rng}


def class_divination():
    import class_rooms  # here, not at the top: class_rooms imports this module when it loads
    from instance_kit import KitRoom

    cell = "class_divination"
    hero = [{"id": "vault", "piece": "kit_vault_barrel_20x16", "pos": [0.0, D_SPRING, 0.0], "yaw": 0.0,
             "args": {"rise": D_RISE, "ribs": 3, "rib0": -D_RIBS, "pitch": D_RIBS, "web": 0}}]
    for t, (x, z, n) in enumerate(TABLES):
        hero.append({"id": f"rug_{t}", "piece": "kit_rug_3x3", "pos": [x, 0.0, round(z + 0.6, 4)], "yaw": 0.0,
                     "args": {"field": 4}})
        hero.append({"id": f"table_{t}", "piece": "kit_table_round", "pos": [x, 0.0, z], "yaw": 0.0})
        hero.append({"id": f"table_{t}_set", "piece": "kit_divination_set", "pos": [x, TABLE_TOP, z], "yaw": 0.0,
                     "args": {"kind": t % 2, "places": n, "centre": 1 if t % 2 == 0 else 2, "spread": SPREAD[n]}})
        for i, (cx, cz, yaw) in enumerate(chair_spots(x, z, n)):
            hero.append({"id": f"armchair_{t}_{i}", "piece": "kit_armchair", "pos": [cx, 0.0, cz], "yaw": yaw,
                         "args": {"cover": ARMCHAIR_COVER}})
    px, pz = PROFESSOR_TABLE
    hero += [
        {"id": "professor_table", "piece": "kit_table_round", "pos": [px, 0.0, pz], "yaw": 0.0,
         "args": {"diameter": 1.2}},
        {"id": "professor_ball", "piece": "kit_divination_set", "pos": [px, TABLE_TOP, pz], "yaw": 0.0,
         "args": {"kind": 1, "places": 0, "centre": 3}},
        {"id": "rug_front", "piece": "kit_rug_6x3", "pos": [0.0, 0.0, -6.3], "yaw": 0.0, "args": {"field": 4}},
        {"id": "shelves_n0", "piece": "kit_shelf_wall", "pos": [-5.0, 0.0, -8.0], "yaw": 0.0,
         "args": {"width": 2.4, "height": 2.4}},
        {"id": "shelves_n1", "piece": "kit_shelf_wall", "pos": [5.0, 0.0, -8.0], "yaw": 0.0,
         "args": {"width": 2.4, "height": 2.4}},
        {"id": "shelves_s0", "piece": "kit_shelf_wall", "pos": [-5.5, 0.0, 8.0], "yaw": PI,
         "args": {"width": 2.4, "height": 2.4}},
        {"id": "shelves_s1", "piece": "kit_shelf_wall", "pos": [5.5, 0.0, 8.0], "yaw": PI,
         "args": {"width": 2.4, "height": 2.4}},
        {"id": "chandelier", "piece": "kit_chandelier_ring", "pos": [0.0, 6.1, -0.5], "yaw": 0.0,
         "args": {"group": 0, "chain": 2.3, "radius": 1.2, "count": 16}},
    ]
    # Wine drapes beside every lancet (the windows' centres: the side walls' bays between the ribs).
    for side, x, yaw in (("w", -10.0, HALF_PI), ("e", 10.0, -HALF_PI)):
        for b, z in enumerate((-6.0, -2.0, 2.0, 6.0)):
            hero.append({"id": f"drapes_{side}{b}", "piece": "kit_drapes_1.6x5.9", "pos": [x, 0.0, z], "yaw": yaw,
                         "args": {"house": 0, "cloth": 1}})
    for i, x in enumerate((-4.6, 4.6)):
        hero.append({"id": f"lantern_{i}", "piece": "kit_lantern_hanging", "pos": [x, 7.85, 3.0], "yaw": 0.0,
                     "args": {"drop": 1.6}})
    spec = {
        "budget": "classroom",
        "materials": {"castle_wall": "castle_wall_b", "castle_floor": "castle_floor_long"},
        "bay_target": 4.0,
        "springing": D_SPRING,
        "runs": {"class_floor:n0": {"bounds": []}, "class_floor:s0": {"bounds": []},
                 "class_floor:w0": {"bounds": [-D_RIBS, 0.0, D_RIBS]}, "class_floor:e0": {"bounds": [-D_RIBS, 0.0, D_RIBS]}},
        "floor_border": True,
        "arches": [],
        "openings": [dict(class_rooms.DOOR),
                     {"kind": "window", "piece": "kit_window_lancet_1.6x5", "run": "class_floor:e0", "bays": [0, 1, 2, 3],
                      "sill": 0.6},
                     {"kind": "window", "piece": "kit_window_lancet_1.6x5", "run": "class_floor:w0", "bays": [0, 1, 2, 3],
                      "sill": 0.6}],
        "hero": hero,
        "replaces": [],
        "shade": {"radius": 2.5, "rays": 64},
    }
    lights = [
        _light("chandelier", (0.0, 5.4, -0.5), "candle", 7.0, 13.0),
        _light("professor", (0.0, 2.2, -6.6), "candle", 4.0, 7.0),
        _light("lanterns_w", (-4.6, 5.0, 3.0), "candle", 5.0, 10.0),
        _light("lanterns_e", (4.6, 5.0, 3.0), "candle", 5.0, 10.0),
        _light("windows_w", (-8.0, 4.0, 0.0), "sky", 3.0, 10.0),
        _light("windows_e", (8.0, 4.0, 0.0), "sky", 3.0, 10.0),
    ]
    views = {
        "entry": ((0.0, 2.6, 7.0), (0.0, 1.2, -6.0), 15),
        "front": ((-2.0, 1.8, -6.6), (2.0, 1.0, 6.0), 16),
        "side": ((8.6, 3.6, -6.8), (-5.0, 1.0, 4.0), 15),
        "tables": ((-1.5, 1.5, 4.6), (-5.0, 0.8, -1.0), 18),
        "ball": ((1.4, 1.5, -4.6), (0.0, 0.9, -7.25), 24),
        "up": ((0.0, 1.6, 5.0), (0.0, 7.0, -4.0), 12),
    }
    layout = class_rooms._layout(cell, "Divination Classroom", D_SPRING + D_RISE)
    return KitRoom("class_divination", cell, "Divination Classroom", layout, spec, [], lights,
                   {"sky": palette.GLOW["candle"], "ground": palette.CASTLE["flagstone"], "intensity": 0.7}, views)


KIT_ROOMS = {"class_divination": class_divination}
