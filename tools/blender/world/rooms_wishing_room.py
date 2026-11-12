"""The two Wishing Rooms (docs/architecture.md
"Secrets and the map"; docs/art/castle-kit.md), built from castle-kit pieces through
the instance-room kit adapter (`instance_kit.py`), as the class rooms are.
Pure Python, no `bpy`. Exported by `build_wishing_room.py` (build_school.py's
export, in a step of its own: the class rooms' set stays as it is); their
layouts are the contract's files, kept equal by tests/test_p3d_secrets.py.

Local coordinates, metres, Y up, x east, z south, as every instance layout.
Both keep the contract's ids and spots exactly (the server embeds them:
`realtime::layouts::WISHING_ROOM_LAYOUTS`): the spawn `wishing_room_entry`, the exit
`wishing_room_exit` (`instance_exit`) before the south wall's door.

* `lost_property_vault`, the Lost Property Vault: a tall hall (24 x 20 m,
  ceiling 12) in the warm castle stone, cluttered with what the castle has
  mislaid over the centuries: towering stacks of unclaimed oddments
  (kit_lost_stack) in rows that leave crooked aisles, crate stacks, a barrel stack, old bookcases,
  trophy cases, a suit of armour and a robed statue among the heaps, hanging
  lanterns high under the ceiling; the Mound of Unclaimed Oddments
  (kit_lost_heap) behind its find spot `find_lost_property_vault`
  [0, 0, -6] (the landmark `wishing_room_heap`). The aisle from the door to the heap stays clear.
* `wishing_room_gathering`, the Gathering Room: a lounge (18 x 16 m, ceiling 6) in the
  library's honey stone: the hooded hearth on the north wall (the landmark
  `wishing_room_hearth`), three sofas and four wing armchairs round a big rug before
  it, two round tables with armchairs, rugs with floor cushions in the
  corners, bookcases, reading lamps, a ring chandelier and the four house
  banners: seats (`seats`) and floor seats (`floor_seats`) for twenty and more.

Colours: castle_* materials and props-atlas palette names only (through the
pieces); light colours from `palette.py`. No colour literal here."""

from __future__ import annotations

import copy
import math

import palette
from instance_kit import KitRoom

PI = round(math.pi, 7)
YAW_PI = 3.1416
HALF_PI = 1.5708
DOOR = {"kind": "blind_door", "piece": "kit_door_blind_3x4.5", "sill": 0, "t": 0}

# The rooms' layouts (data/world/instances/lost_property_vault.json and wishing_room_gathering.json), kept equal to the files.
HIDDEN_LAYOUT = {
    "version": 1,
    "surfaces": [{"id": "lost_property_floor", "kind": "room", "name": "Lost Property Vault", "level": 0,
                  "cell": "lost_property_vault", "min": [-12.0, -10.0], "max": [12.0, 10.0], "y": 0.0, "ceiling": 12.0}],
    "spawns": [{"id": "wishing_room_entry", "pos": [0.0, 0.0, 8.0], "yaw": PI}],
    "landmarks": [{"id": "wishing_room_heap", "name": "Mound of Unclaimed Oddments", "pos": [0.0, 0.0, -7.0]}],
    "interactables": [
        {"id": "wishing_room_exit", "kind": "instance_exit", "name": "Door", "pos": [0.0, 0.0, 9.3], "yaw": PI, "radius": 1.5},
        {"id": "find_lost_property_vault", "kind": "find_spot", "name": "Mound of Unclaimed Oddments", "pos": [0.0, 0.0, -6.0],
         "radius": 1.8}],
}
GATHERING_LAYOUT = {
    "version": 1,
    "surfaces": [{"id": "wishing_room_gathering_floor", "kind": "room", "name": "Gathering Room", "level": 0,
                  "cell": "wishing_room_gathering", "min": [-9.0, -8.0], "max": [9.0, 8.0], "y": 0.0, "ceiling": 6.0}],
    "spawns": [{"id": "wishing_room_entry", "pos": [0.0, 0.0, 6.0], "yaw": PI}],
    "landmarks": [{"id": "wishing_room_hearth", "name": "Hearth", "pos": [0.0, 0.0, -7.0]}],
    "interactables": [
        {"id": "wishing_room_exit", "kind": "instance_exit", "name": "Door", "pos": [0.0, 0.0, 7.3], "yaw": PI, "radius": 1.5}],
}


def _light(lid: str, pos, glow: str, intensity: float, rng: float) -> dict:
    return {"id": lid, "kind": "point", "pos": [float(v) for v in pos], "color": palette.GLOW[glow],
            "intensity": intensity, "range": rng}


def _h(hid: str, piece: str, pos, yaw: float = 0.0, **args) -> dict:
    e = {"id": hid, "piece": piece, "pos": [float(v) for v in pos], "yaw": yaw}
    if args:
        e["args"] = args
    return e


# ---------------------------------------------------------------------------
# The Lost Property Vault

# The stacks of unclaimed oddments: (x, z, height, seed); rows either side of the clear aisle
# (|x| < 2.2) from the door to the heap, crooked lanes between them.
HIDDEN_STACKS = (
    (-3.6, 5.6, 4.2, 1), (-4.2, 2.4, 5.6, 2), (-3.4, -1.0, 6.4, 3), (-4.0, -4.2, 5.0, 4),
    (3.8, 5.0, 4.6, 5), (3.4, 1.6, 6.2, 6), (4.2, -1.8, 5.4, 7), (3.6, -4.8, 6.0, 8),
    (-7.6, 6.6, 3.8, 9), (-8.0, 3.0, 5.2, 10), (-7.4, -0.6, 6.6, 11), (-8.2, -4.4, 4.4, 12),
    (7.8, 6.2, 5.0, 13), (8.2, 2.6, 4.0, 14), (7.6, -1.0, 6.8, 15), (8.0, -4.6, 5.6, 16),
    (-10.4, 5.0, 4.8, 17), (-10.4, 1.2, 6.0, 18), (10.4, 0.0, 5.2, 19), (10.4, -3.2, 6.4, 20),
    (-9.6, -8.4, 5.8, 21), (9.4, 7.8, 4.4, 22), (4.8, 8.6, 3.6, 23),
)


def lost_property_vault() -> KitRoom:
    cell = "lost_property_vault"
    hero = [_h("heap", "kit_lost_heap", (0.0, 0.0, -6.9), 0.0, seed=7, radius=2.6),
            _h("heap_w", "kit_lost_heap", (-8.6, 0.0, -6.4), 0.6, seed=8, radius=1.8),
            _h("heap_e", "kit_lost_heap", (6.2, 0.0, 8.0), 2.8, seed=9, radius=1.6)]
    for k, (x, z, h, seed) in enumerate(HIDDEN_STACKS):
        hero.append(_h(f"stack_{k}", "kit_lost_stack", (x, 0.0, z), round(0.37 * k % 1.2 - 0.6, 4), seed=seed, height=h))
    hero += [
        _h("crates_w", "kit_crate_stack", (-10.9, 0.0, 7.6), HALF_PI),
        _h("crates_e", "kit_crate_stack", (10.9, 0.0, -7.8), -HALF_PI),
        _h("crates_n", "kit_crate_stack", (-6.0, 0.0, -9.2), 0.0),
        _h("barrels", "kit_barrel_stack", (6.6, 0.0, -10.0), 0.0),
        _h("barrels_sw", "kit_barrel_stack", (-6.6, 0.0, 10.0), YAW_PI),
        _h("bookcase_w", "kit_bookcase_3.6x3", (-12.0, 0.0, -2.5), HALF_PI, seed=41),
        _h("bookcase_e", "kit_bookcase_2x3", (12.0, 0.0, 7.5), -HALF_PI, seed=42, cupboard=1),
        _h("case_w", "kit_trophy_case", (-12.0, 0.0, -7.5), HALF_PI, width=2.4, seed=43, house=1),
        _h("case_e", "kit_trophy_case", (12.0, 0.0, 2.5), -HALF_PI, width=2.4, seed=44, house=2),
        _h("statue_w", "kit_statue_scholar", (-1.9, 0.0, -3.6), 0.5),
        _h("statue_e", "kit_statue_scholar", (2.6, 0.0, -9.0), -0.4),
        _h("armchair_0", "kit_armchair", (-6.0, 0.0, 8.6), 2.6, cover=4),
        _h("armchair_1", "kit_armchair", (5.8, 0.0, 8.4), -2.4, cover=5),
    ]
    for i, (x, z) in enumerate(((-6.0, -6.0), (6.0, -6.0), (-6.0, 2.0), (6.0, 2.0), (0.0, -2.0))):
        hero.append(_h(f"lantern_{i}", "kit_lantern_hanging", (x, 11.6, z), 0.0, drop=2.5))
    spec = {
        "budget": "classroom",
        "materials": {"castle_wall": "castle_wall_a", "castle_floor": "castle_floor_long"},
        "bay_target": 4.8,
        "runs": {"lost_property_floor:n0": {"bounds": [-7.0, -2.5, 2.5, 7.0]},
                 "lost_property_floor:s0": {"bounds": [-7.0, -2.5, 2.5, 7.0]},
                 "lost_property_floor:w0": {"bounds": [-5.0, 0.0, 5.0]}, "lost_property_floor:e0": {"bounds": [-5.0, 0.0, 5.0]}},
        "floor_border": True,
        "arches": [],
        "openings": [dict(DOOR, run="lost_property_floor:s0"),
                     {"kind": "window", "piece": "kit_window_lancet_1.6x5", "run": "lost_property_floor:n0", "bays": [0, 4],
                      "sill": 6.0}],
        "lanterns": {"piece": "kit_lantern_wall", "every": 2, "y": 4.2},
        "hero": hero,
        "replaces": [],
        "shade": {"radius": 2.5, "rays": 64},
    }
    lights = [
        _light("heap", (0.0, 4.0, -5.0), "candle", 7.0, 12.0),
        _light("lanterns_w", (-6.0, 8.5, -2.0), "candle", 6.0, 14.0),
        _light("lanterns_e", (6.0, 8.5, -2.0), "candle", 6.0, 14.0),
        _light("door", (0.0, 4.0, 7.0), "candle", 5.0, 12.0),
        _light("windows_n", (0.0, 9.0, -8.5), "sky", 4.0, 14.0),
    ]
    views = {
        "entry": ((0.0, 1.8, 8.6), (0.0, 1.6, -7.0), 16),
        "heap": ((1.4, 1.7, -2.6), (0.0, 1.2, -8.0), 18),
        "aisle": ((-5.8, 1.8, 8.4), (-5.6, 3.0, -6.0), 15),
        "high": ((10.6, 9.5, 8.6), (-4.0, 2.0, -5.0), 16),
    }
    return KitRoom("lost_property_vault", cell, "Lost Property Vault", copy.deepcopy(HIDDEN_LAYOUT), spec, [], lights,
                   {"sky": palette.GLOW["candle"], "ground": palette.CASTLE["flagstone"], "intensity": 0.6}, views)


# ---------------------------------------------------------------------------
# The Gathering Room


def wishing_room_gathering() -> KitRoom:
    cell = "wishing_room_gathering"
    hero = [
        _h("rug_fire", "kit_rug_8x5", (0.0, 0.0, -3.4), 0.0, field=4),
        _h("sofa_front", "kit_sofa_3.2", (0.0, 0.0, -0.4), YAW_PI, cover=0),
        _h("sofa_w", "kit_sofa_3.2", (-3.8, 0.0, -3.6), HALF_PI, cover=2),
        _h("sofa_e", "kit_sofa_3.2", (3.8, 0.0, -3.6), -HALF_PI, cover=1),
        _h("armchair_nw", "kit_armchair", (-3.4, 0.0, -6.4), 0.9, cover=3),
        _h("armchair_ne", "kit_armchair", (3.4, 0.0, -6.4), -0.9, cover=5),
        _h("table_fire", "kit_table_round", (0.0, 0.0, -3.4), 0.0),
        # The corners: round tables with armchairs; rugs with floor cushions.
        _h("table_sw", "kit_table_round", (-6.6, 0.0, 4.4), 0.0),
        _h("armchair_sw0", "kit_armchair", (-6.6, 0.0, 3.2), 0.0, cover=4),
        _h("armchair_sw1", "kit_armchair", (-6.6, 0.0, 5.6), YAW_PI, cover=4),
        _h("table_se", "kit_table_round", (6.6, 0.0, 4.4), 0.0),
        _h("armchair_se0", "kit_armchair", (6.6, 0.0, 3.2), 0.0, cover=5),
        _h("armchair_se1", "kit_armchair", (6.6, 0.0, 5.6), YAW_PI, cover=5),
        _h("rug_w", "kit_rug_3x4", (-7.0, 0.0, -1.6), 0.0, field=2),
        _h("rug_e", "kit_rug_3x4", (7.0, 0.0, -1.6), 0.0, field=1),
        _h("rug_s", "kit_rug_5x2.5", (0.0, 0.0, 4.2), 0.0, field=0),
        _h("bookcase_w", "kit_bookcase_3.6x3", (-9.0, 0.0, -5.6), HALF_PI, seed=51),
        _h("bookcase_e", "kit_bookcase_3.6x3", (9.0, 0.0, -5.6), -HALF_PI, seed=52),
        _h("bookcase_sw", "kit_bookcase_3.6x3", (-6.25, 0.0, 8.0), YAW_PI, seed=53),
        _h("bookcase_se", "kit_bookcase_3.6x3", (6.25, 0.0, 8.0), YAW_PI, seed=54),
        _h("bookcase_w2", "kit_bookcase_2x3", (-9.0, 0.0, 5.25), HALF_PI, seed=55),
        _h("bookcase_e2", "kit_bookcase_2x3", (9.0, 0.0, 5.25), -HALF_PI, seed=56),
        _h("lamp_sw", "kit_lamp_reading", (-6.6, 0.74, 4.4), 0.0),
        _h("lamp_se", "kit_lamp_reading", (6.6, 0.74, 4.4), 0.0),
        _h("banner_a", "kit_banner_a", (-6.2, 5.4, -8.0), 0.0, width=1.2, height=3.0),
        _h("banner_b", "kit_banner_b", (6.2, 5.4, -8.0), 0.0, width=1.2, height=3.0),
        _h("banner_c", "kit_banner_c", (-9.0, 5.4, 0.0), HALF_PI, width=1.2, height=3.0),
        _h("banner_d", "kit_banner_d", (9.0, 5.4, 0.0), -HALF_PI, width=1.2, height=3.0),
    ]
    for i, (x, z, cover) in enumerate(((-7.6, -2.6, 0), (-6.4, -0.6, 1), (-7.6, -0.6, 2), (6.4, -2.6, 3),
                                       (7.6, -0.6, 4), (6.4, -0.6, 0), (-1.4, 4.2, 1), (1.4, 4.2, 3))):
        hero.append(_h(f"cushion_{i}", "kit_floor_cushion", (x, 0.02, z), round(0.3 * i, 4), cover=cover))
    spec = {
        "budget": "classroom",
        "materials": {"castle_wall": "castle_wall_b", "castle_floor": "castle_floor_long"},
        "bay_target": 4.0,
        "runs": {"wishing_room_gathering_floor:n0": {"bounds": [-3.5, 3.5]}, "wishing_room_gathering_floor:s0": {"bounds": [-3.5, 3.5]},
                 "wishing_room_gathering_floor:w0": {"bounds": [-2.5, 2.5]}, "wishing_room_gathering_floor:e0": {"bounds": [-2.5, 2.5]}},
        "floor_border": True,
        "arches": [],
        "openings": [dict(DOOR, run="wishing_room_gathering_floor:s0"),
                     {"kind": "fireplace", "piece": "kit_fireplace_hooded_4.4x3.6", "run": "wishing_room_gathering_floor:n0",
                      "bays": [1], "sill": 0}],
        "lanterns": {"piece": "kit_lantern_wall", "every": 1, "y": 3.4},
        "hero": hero,
        "replaces": [],
        "shade": {"radius": 2.5, "rays": 64},
    }
    lights = [
        _light("hearth", (0.0, 1.2, -7.0), "fire", 8.0, 12.0),
        _light("lamps_w", (-6.6, 2.0, 4.4), "candle", 4.0, 8.0),
        _light("lamps_e", (6.6, 2.0, 4.4), "candle", 4.0, 8.0),
        _light("lanterns", (0.0, 4.0, 2.0), "candle", 5.0, 12.0),
    ]
    views = {
        "entry": ((0.0, 1.8, 6.8), (0.0, 1.2, -7.0), 15),
        "hearth": ((-4.6, 1.6, 1.8), (1.0, 1.0, -6.0), 16),
        "corner": ((3.0, 1.7, -1.0), (-7.0, 0.8, 4.4), 16),
        "high": ((7.8, 5.2, 7.2), (-3.0, 0.6, -3.0), 15),
    }
    return KitRoom("wishing_room_gathering", cell, "Gathering Room", copy.deepcopy(GATHERING_LAYOUT), spec, [], lights,
                   {"sky": palette.GLOW["candle"], "ground": palette.CASTLE["flagstone"], "intensity": 0.7}, views)


KIT_ROOMS = {"lost_property_vault": lost_property_vault, "wishing_room_gathering": wishing_room_gathering}
