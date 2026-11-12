"""The class instance rooms built from castle-kit pieces
(the instance-room kit adapter, `instance_kit.py`;
docs/architecture.md). Pure Python, no `bpy`.

Local coordinates, metres, Y up, as every instance layout: x east, z south.
Both rooms keep the contract's ids and spots: the floor `class_floor`
(20 x 16 m), the spawns `class_entry_a/b` by the door, the exit
`class_exit` at the south wall's class door, the professor's spot
`class_professor` at [0, 0, -6] facing +z (the students).

* `class_potions` (Potions, Professor Greer): a dungeon vault in the cool
  dungeon stone (`castle_wall_d`, `castle_floor_dungeon`), a round barrel
  vault in the same stone on three ribs, eight cauldron desks (the castle
  Potions classroom's `classroom_cauldron`) in two rows facing the front,
  shelves of jars along the west and north walls, the Potions master's
  desk before the blackboard, an apothecary cupboard and barrels by the
  door, wall torches and two iron chandeliers.
* `class_classroom` (Transfiguration; the `class_defence` is the same
  shell with a drill dressing, school_rooms_defence.py): a tall vaulted classroom in the pale castle stone, four lancet
  windows east, bookcases west and north, sixteen study tables with
  thirty-two chairs facing the front (seat rows for the sit emote), each
  table dressed with books, inkwells and the lesson's small practice
  objects (`kit_desk_objects`), the professor's table before the
  blackboard, wall lanterns on the shafts.

Colours: every surface is a castle_* material (castle_textures.json) or a
props-atlas slot of an existing model or palette name; light and ambient
colours come from `palette.py` (GLOW, CASTLE). No colour literal here.
"""

from __future__ import annotations

import importlib
import math

import palette
from instance_kit import KitRoom

X0, X1, Z0, Z1 = -10.0, 10.0, -8.0, 8.0
PROFESSOR = [0.0, 0.0, -6.0]
PI = round(math.pi, 7)
HALF_PI = round(math.pi / 2, 4)


def _layout(cell: str, name: str, ceiling: float) -> dict:
    return {
        "version": 1,
        "surfaces": [{"id": "class_floor", "kind": "room", "name": name, "level": 0, "cell": cell, "min": [X0, Z0],
                      "max": [X1, Z1], "y": 0.0, "ceiling": ceiling}],
        "spawns": [{"id": "class_entry_a", "pos": [-1.0, 0.0, 5.5], "yaw": PI},
                   {"id": "class_entry_b", "pos": [1.0, 0.0, 5.5], "yaw": PI}],
        "landmarks": [{"id": "class_professor", "name": "Professor's Desk", "pos": list(PROFESSOR)}],
        "interactables": [{"id": "class_exit", "kind": "instance_exit", "name": "Classroom Door", "pos": [0.0, 0.0, 7.5],
                           "yaw": PI, "radius": 1.5}],
    }


def _light(lid: str, pos, glow: str, intensity: float, rng: float) -> dict:
    return {"id": lid, "kind": "point", "pos": [float(v) for v in pos], "color": palette.GLOW[glow],
            "intensity": intensity, "range": rng}


def _vault(springing: float, rise: float, web: int, pitch: float) -> dict:
    """A round barrel vault along z over the whole room, three transverse
    ribs at z -pitch, 0, pitch (on the bay shafts, or on corbels where a
    wall has none: rules.m5b_dungeons)."""
    return {"id": "vault", "piece": f"kit_vault_barrel_{int(X1 - X0)}x{int(Z1 - Z0)}", "pos": [0.0, springing, 0.0],
            "yaw": 0.0, "args": {"rise": rise, "ribs": 3, "rib0": -pitch, "pitch": pitch, "web": web}}


DOOR = {"kind": "blind_door", "piece": "kit_door_class_3x4.5", "run": "class_floor:s0", "t": 0, "sill": 0}


# ---------------------------------------------------------------------------
# Potions


P_SPRING, P_RISE = 4.2, 3.0
P_RIBS = 5.5  # the ribs at z -5.5, 0, 5.5: the west wall's shelves stand between their corbels
CAULDRON_XS = (-6.9, -2.5, 1.9, 6.3)  # the model's bench reaches +1.1 m east of its fire
CAULDRON_ZS = (-2.6, 1.4)


def class_potions() -> KitRoom:
    cell = "class_potions"
    torches = [{"id": f"torch_e{i}", "piece": "kit_torch_wall", "pos": [X1, 0.0, z], "yaw": -HALF_PI,
                "args": {"height": 2.8}} for i, z in enumerate((-6.75, -2.75, 2.75, 6.75))]
    torches += [{"id": f"torch_w{i}", "piece": "kit_torch_wall", "pos": [X0, 0.0, z], "yaw": HALF_PI,
                 "args": {"height": 3.0}} for i, z in enumerate((-6.75, 6.75))]
    spec = {
        "budget": "classroom",
        "materials": {"castle_wall": "castle_wall_d", "castle_floor": "castle_floor_dungeon"},
        "bay_target": 4.0,
        "springing": P_SPRING,
        "runs": {"class_floor:n0": {"bounds": []}, "class_floor:s0": {"bounds": []}, "class_floor:w0": {"bounds": []},
                 "class_floor:e0": {"bounds": [-P_RIBS, 0.0, P_RIBS]}},
        "floor_border": True,
        "arches": [],
        "openings": [dict(DOOR)],
        "hero": [_vault(P_SPRING, P_RISE, 1, P_RIBS), *torches],
        "replaces": [],
        "shade": {"radius": 2.5, "rays": 64},
    }
    kit = []
    for r, z in enumerate(CAULDRON_ZS):
        for c, x in enumerate(CAULDRON_XS):
            kit.append({"id": f"cauldron_{r}{c}", "model": "classroom_cauldron", "pos": [x, 0.0, z], "yaw": 0.0})
    kit += [
        {"id": "shelves_w0", "model": "potions_shelves", "pos": [X0, 0.0, -2.75], "yaw": HALF_PI},
        {"id": "shelves_w1", "model": "potions_shelves", "pos": [X0, 0.0, 2.75], "yaw": HALF_PI},
        {"id": "shelves_n0", "model": "potions_shelves", "pos": [-5.6, 0.0, Z0], "yaw": 0.0},
        {"id": "shelves_n1", "model": "potions_shelves", "pos": [5.6, 0.0, Z0], "yaw": 0.0},
        {"id": "blackboard", "model": "potions_blackboard", "pos": [0.0, 0.0, Z0], "yaw": 0.0},
        {"id": "master_desk", "model": "teacher_desk", "pos": [0.0, 0.0, -7.2], "yaw": 0.0},
        {"id": "cupboard", "model": "apothecary_cupboard", "pos": [-6.0, 0.0, Z1], "yaw": PI},
        {"id": "barrels", "model": "barrels", "pos": [6.5, 0.0, 7.2], "yaw": 0.4},
        {"id": "chandelier_n", "model": "iron_chandelier", "pos": [0.0, P_SPRING + P_RISE - 0.05, -2.75], "yaw": 0.0},
        {"id": "chandelier_s", "model": "iron_chandelier", "pos": [0.0, P_SPRING + P_RISE - 0.05, 2.75], "yaw": 0.3},
    ]
    lights = [
        _light("cauldrons_front", (0.0, 1.2, CAULDRON_ZS[0]), "green", 6.0, 9.0),
        _light("chandelier_n", (0.0, 5.4, -2.75), "candle", 7.0, 12.0),
        _light("cauldrons_back", (0.0, 1.2, CAULDRON_ZS[1]), "green", 5.0, 9.0),
        _light("chandelier_s", (0.0, 5.4, 2.75), "candle", 6.0, 12.0),
        _light("torch_e", (9.6, 2.9, -2.75), "fire", 3.0, 7.0),
        _light("torch_w", (-9.6, 3.1, 6.75), "fire", 3.0, 7.0),
    ]
    views = {
        "entry": ((0.0, 2.6, 7.0), (0.0, 1.2, -6.0), 15),
        "front": ((-2.0, 1.8, -6.6), (2.0, 1.2, 6.0), 16),
        "side": ((8.6, 3.4, -6.8), (-5.0, 1.0, 4.0), 15),
        "shelves": ((3.0, 1.7, 0.0), (-10.0, 1.6, 0.0), 18),
        "up": ((0.0, 1.6, 5.0), (0.0, 7.0, -4.0), 12),
    }
    return KitRoom("class_potions", cell, "Potions Classroom", _layout(cell, "Potions Classroom", P_SPRING + P_RISE), spec,
                   kit, lights, {"sky": palette.CASTLE["stone_dungeon"], "ground": palette.CASTLE["flagstone_dungeon"],
                                 "intensity": 0.7}, views)


# ---------------------------------------------------------------------------
# The shared classroom (Transfiguration's desks)


C_SPRING, C_RISE = 6.6, 3.0
C_RIBS = 4.0  # the ribs at z -4, 0, 4, on both side walls' shafts
DESK_XS = (-6.75, -2.25, 2.25, 6.75)
DESK_ZS = (-3.5, -1.5, 0.5, 2.5)
TABLE_TOP = 0.76  # kit_table_study's top (room_furniture)
LANTERN_HOOK = 8.9  # under the vault at x +-4.5 (its web is at 9.03 there)
CHAIR_BEHIND = 1.1  # chair centre behind the table's centre (the table 0.65 ahead of the seat line: knee room)


def class_classroom() -> KitRoom:
    cell = "class_classroom"
    hero = [_vault(C_SPRING, C_RISE, 0, C_RIBS)]
    k = 0
    for z in DESK_ZS:
        for x in DESK_XS:
            hero.append({"id": f"desk_{k}", "piece": "kit_table_study", "pos": [x, 0.0, z], "yaw": 0.0, "args": {"length": 1.6}})
            hero.append({"id": f"desk_{k}_things", "piece": "kit_desk_objects", "pos": [x, TABLE_TOP, z], "yaw": 0.0,
                         "args": {"seed": k + 1}})
            for i, dx in enumerate((-0.4, 0.4)):
                hero.append({"id": f"chair_{k}_{i}", "piece": "kit_chair_study", "pos": [x + dx, 0.0, z + CHAIR_BEHIND],
                             "yaw": PI})
            k += 1
    hero += [
        {"id": "professor_table", "piece": "kit_table_study", "pos": [0.0, 0.0, -7.2], "yaw": 0.0, "args": {"length": 2.4}},
        {"id": "bookcase_n0", "piece": "kit_bookcase_3x4.2", "pos": [-6.2, 0.0, Z0], "yaw": 0.0, "args": {"seed": 21}},
        {"id": "bookcase_n1", "piece": "kit_bookcase_3x4.2", "pos": [6.2, 0.0, Z0], "yaw": 0.0, "args": {"seed": 22}},
        {"id": "bookcase_w0", "piece": "kit_bookcase_3x4.2", "pos": [X0, 0.0, -6.0], "yaw": HALF_PI, "args": {"seed": 23}},
        {"id": "bookcase_w2", "piece": "kit_bookcase_3x4.2", "pos": [X0, 0.0, 2.0], "yaw": HALF_PI, "args": {"seed": 24}},
        {"id": "bookcase_w3", "piece": "kit_bookcase_3x4.2", "pos": [X0, 0.0, 6.0], "yaw": HALF_PI, "args": {"seed": 25}},
        {"id": "rug_front", "piece": "kit_rug_4x3", "pos": [0.0, 0.0, -5.6], "yaw": 0.0, "args": {"field": 4}},
    ]
    # Hanging lanterns from the vault between the ribs, over the desks.
    for i, (x, z) in enumerate(((-4.5, -2.0), (4.5, -2.0), (-4.5, 2.0), (4.5, 2.0))):
        hero.append({"id": f"lantern_{i}", "piece": "kit_lantern_hanging", "pos": [x, LANTERN_HOOK, z], "yaw": 0.0,
                     "args": {"drop": 1.5}})
    spec = {
        "budget": "classroom",
        "materials": {"castle_wall": "castle_wall_a", "castle_floor": "castle_floor_long"},
        "bay_target": 4.0,
        "springing": C_SPRING,
        "runs": {"class_floor:n0": {"bounds": []}, "class_floor:s0": {"bounds": []},
                 "class_floor:w0": {"bounds": [-4.0, 0.0, 4.0]}, "class_floor:e0": {"bounds": [-4.0, 0.0, 4.0]}},
        "floor_border": True,
        "arches": [],
        "openings": [dict(DOOR),
                     {"kind": "window", "piece": "kit_window_lancet_1.6x5", "run": "class_floor:e0", "bays": [0, 1, 2, 3],
                      "sill": 1.2}],
        "lanterns": {"piece": "kit_lantern_wall", "every": 1, "y": 3.6},
        "hero": hero,
        "replaces": [],
        "shade": {"radius": 2.5, "rays": 64},
    }
    kit = [
        {"id": "blackboard", "model": "potions_blackboard", "pos": [0.0, 0.0, Z0], "yaw": 0.0},
        {"id": "practice_table", "model": "transfiguration_table", "pos": [X0, 0.0, -2.0], "yaw": HALF_PI},
    ]
    lights = [
        _light("windows_n", (7.5, 5.0, -4.0), "sky", 7.0, 14.0),
        _light("windows_s", (7.5, 5.0, 4.0), "sky", 7.0, 14.0),
        _light("front", (0.0, 4.5, -6.0), "candle", 6.0, 12.0),
        _light("lanterns_w", (-8.0, 4.0, 0.0), "candle", 5.0, 12.0),
        _light("back", (0.0, 4.5, 4.5), "candle", 5.0, 12.0),
    ]
    views = {
        "entry": ((0.0, 2.6, 7.0), (0.0, 1.2, -6.0), 15),
        "front": ((-2.0, 1.8, -6.6), (2.0, 1.0, 6.0), 16),
        "side": ((8.6, 3.6, -6.8), (-5.0, 1.0, 4.0), 15),
        "desks": ((-4.5, 1.5, 4.6), (0.0, 0.8, -1.5), 18),
        "up": ((0.0, 1.6, 5.0), (0.0, 9.0, -4.0), 12),
    }
    return KitRoom("class_classroom", cell, "Classroom", _layout(cell, "Classroom", C_SPRING + C_RISE), spec, kit, lights,
                   {"sky": palette.GLOW["sky"], "ground": palette.CASTLE["flagstone"], "intensity": 0.8}, views)


KIT_ROOMS = {"class_classroom": class_classroom, "class_potions": class_potions}

# ---- The new rooms live in their own modules,
# `school_rooms_<room>.py` next to this file, each exporting
# `KIT_ROOMS = {layout id: builder}` (Rooms A: school_rooms_divination.py,
# and class_defence, a drill dressing of class_classroom's shell, in this
# file or its own module; Rooms B: school_rooms_astronomy.py). A module not
# written yet is skipped, so its placeholder layout stays.
#
# ---- school_rooms_greenhouse.py (class_greenhouse) and
# school_rooms_paddock.py (class_paddock). A module may also export `CLASS_PROPS = {layout id:
# {prop id: builder}}`: the room's animated props (the greenhouse's plants,
# the paddock's creatures), each exported by build_school.py as its own
# glb, `instances/props/<prop id>.glb`, standing at the room layout's
# landmark of the same id (docs/architecture.md).
CLASS_PROPS: dict = {}
for _module in ("school_rooms_defence", "school_rooms_divination", "school_rooms_astronomy",
                "school_rooms_greenhouse", "school_rooms_paddock", "school_rooms_pitch"):
    try:
        _mod = importlib.import_module(_module)
    except ModuleNotFoundError as _e:
        if _e.name != _module:
            raise
        continue
    KIT_ROOMS.update(_mod.KIT_ROOMS)
    CLASS_PROPS.update(getattr(_mod, "CLASS_PROPS", {}))


def build_all() -> dict[str, KitRoom]:
    return {lid: fn() for lid, fn in KIT_ROOMS.items()}
