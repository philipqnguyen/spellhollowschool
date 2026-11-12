"""The Herbology class room, `class_greenhouse`,
built from castle-kit pieces like the rooms (instance_kit.py;
class_rooms.py merges this module's KIT_ROOMS and CLASS_PROPS;
docs/architecture.md). Pure Python at import: the plants' glb
builders (CLASS_PROPS) use `bpy` only when called (build_school.py).

A glasshouse on the grounds (Professor Fernsby's class): the floor is a
`grounds` surface (flagstones) with no ceiling, so the Planner walls it with
the grounds' low wall (1 m), the glasshouse's dwarf wall; on it stands the
glasshouse (`kit_glasshouse`): glazed walls and gables of opaque,
palette-tinted panes between thin iron glazing bars, a pitched glass roof
on a north-south ridge, three iron king-post trusses across. The class door
stands in a stone porch in the south gable (`kit_glasshouse_porch`,
instance_kit.KitRoom.head_door, as the Astronomy stair head). The client
shows no sky here: the panes are opaque and face in.

Thirty students stand at six oak potting benches (`kit_potting_bench`),
three rows either side of the middle aisle, a clay pot with a seedling at
every place (five a bench, facing the professor). Before the professor a
low staging bench (`kit_plant_staging`) holds the four class plants' big
pots, their earth at the layout landmarks `plant_vine`, `plant_root`,
`plant_briar`, `plant_fern` (the contract's spots); the plants themselves
are the animated props of CLASS_PROPS, each its own glb
(`instances/props/<id>.glb`, built by greenhouse_plants.py) that the client
stands at its landmark and sways, lunges, recoils and snaps by node
transforms. The professor's own bench behind her, potting benches of
seedlings along both side walls, copper pots of big leafy plants in the
corners and by the door, and lanterns hung from the trusses' ties.

The contract's ids and spots: the floor `class_floor` (20 x 16 m), the
spawns `class_entry_a/b` at z 5.5, the exit `class_exit` at the porch's door
(z 7.5), the professor's spot `class_professor` at [0, 0, -6] facing +z.

Colours: castle_* materials and props-atlas palette names only; light and
ambient colours from `palette.py` (GLOW, CASTLE). No colour literal here.
"""

from __future__ import annotations

import math

import layout_lib as L
import palette
from castle_kit import grid as G
from castle_kit.pieces import greenhouse as GH
from instance_kit import KitRoom

X0, X1, Z0, Z1 = -10.0, 10.0, -8.0, 8.0
PROFESSOR = [0.0, 0.0, -6.0]
PI = round(math.pi, 7)
HALF_PI = round(math.pi / 2, 4)
CELL = "class_greenhouse"
NAME = "Greenhouse"

LOW_WALL = L.OPEN_WALL_HEIGHT["grounds"]  # the glasshouse's dwarf wall
EAVES, RISE = 3.6, 4.6
PORCH = {"width": 6.0, "height": 5.6, "depth": 0.8}
STAGING_Z = -4.0  # the class plants' staging bench (the plant_* landmarks' z)
PLANTS = (("plant_vine", "Gnashvine"), ("plant_root", "Shriekroot"), ("plant_briar", "Needlebriar"),
          ("plant_fern", "Shiverfern"))
PLANT_PITCH = 2.0
BENCH_XS = (-4.25, 4.25)  # the students' benches either side of the aisle
BENCH_ZS = (-1.0, 1.8, 4.6)
BENCH_LENGTH = 4.0
SIDE_X = 9.4  # the side walls' benches (their backs 0.2 m off the low wall)
SIDE_ZS = (-5.0, -0.5, 4.0)
# Copper pots of big leafy plants (kit_plant_pot): the corners, along the north wall, by the door.
PLANT_POTS = ((-8.9, -7.1), (8.9, -7.1), (-8.9, 7.1), (8.9, 7.1), (-6.2, -7.3), (6.2, -7.3), (-3.6, -7.3), (3.6, -7.3),
              (-4.0, 7.2), (4.0, 7.2))


def plant_landmarks() -> list[dict]:
    """The class plants' spots: on their pots' earth on the staging bench."""
    n = len(PLANTS)
    return [{"id": pid, "name": name, "pos": [round((k - (n - 1) / 2.0) * PLANT_PITCH, 4), GH.PLANT_SOIL_Y, STAGING_Z]}
            for k, (pid, name) in enumerate(PLANTS)]


def _layout() -> dict:
    return {
        "version": 1,
        "surfaces": [{"id": "class_floor", "kind": "grounds", "name": NAME, "level": 0, "cell": CELL, "min": [X0, Z0],
                      "max": [X1, Z1], "y": 0.0}],
        "spawns": [{"id": "class_entry_a", "pos": [-1.0, 0.0, 5.5], "yaw": PI},
                   {"id": "class_entry_b", "pos": [1.0, 0.0, 5.5], "yaw": PI}],
        "landmarks": [{"id": "class_professor", "name": "Professor's Desk", "pos": list(PROFESSOR)}, *plant_landmarks()],
        "interactables": [{"id": "class_exit", "kind": "instance_exit", "name": "Greenhouse Door", "pos": [0.0, 0.0, 7.5],
                           "yaw": PI, "radius": 1.5}],
    }


def _light(lid: str, pos, glow: str, intensity: float, rng: float) -> dict:
    return {"id": lid, "kind": "point", "pos": [float(v) for v in pos], "color": palette.GLOW[glow],
            "intensity": intensity, "range": rng}


def truss_zs() -> list[float]:
    """The trusses' z (on the glasshouse's rafters, as kit_glasshouse places them)."""
    p = GH.glasshouse(**glasshouse_args())
    hz = p.meta["glazing"][1]
    zs = GH._splits(-hz, hz, GH.BAY)
    n = p.meta["trusses"]
    return [round(zs[int(round((k + 1) * (len(zs) - 1) / (n + 1)))], 4) for k in range(n)]


def glasshouse_args() -> dict:
    return {"width": X1 - X0, "depth": Z1 - Z0, "sill": LOW_WALL, "eaves": EAVES, "rise": RISE, "door": PORCH["width"],
            "door_top": PORCH["height"], "trusses": 3}


def class_greenhouse() -> KitRoom:
    hero = [
        {"id": "glasshouse", "piece": "kit_glasshouse", "pos": [0.0, 0.0, 0.0], "yaw": 0.0, "args": glasshouse_args()},
        {"id": "porch", "piece": "kit_glasshouse_porch", "pos": [0.0, 0.0, Z1], "yaw": PI, "args": dict(PORCH)},
        {"id": "plant_staging", "piece": "kit_plant_staging", "pos": [0.0, 0.0, STAGING_Z], "yaw": 0.0,
         "args": {"length": 8.4, "count": len(PLANTS), "pitch": PLANT_PITCH}},
        {"id": "professor_bench", "piece": "kit_potting_bench", "pos": [0.0, 0.0, -7.3], "yaw": 0.0,
         "args": {"length": 2.4, "places": 3, "seed": 40}},
    ]
    k = 0
    for z in BENCH_ZS:
        for x in BENCH_XS:
            hero.append({"id": f"bench_{k}", "piece": "kit_potting_bench", "pos": [x, 0.0, z], "yaw": 0.0,
                         "args": {"length": BENCH_LENGTH, "places": 5, "seed": k + 1}})
            k += 1
    for side, x, yaw in (("w", -SIDE_X, HALF_PI), ("e", SIDE_X, -HALF_PI)):
        for i, z in enumerate(SIDE_ZS):
            hero.append({"id": f"side_{side}{i}", "piece": "kit_potting_bench", "pos": [x, 0.0, z], "yaw": yaw,
                         "args": {"length": BENCH_LENGTH, "places": 5, "seed": 20 + i + (3 if side == "e" else 0),
                                  "grown": 1.0}})
    for i, (x, z) in enumerate(PLANT_POTS):
        hero.append({"id": f"plant_pot_{i}", "piece": "kit_plant_pot", "pos": [x, 0.0, z], "yaw": round(0.7 * i, 4)})
    tie = EAVES - 0.07
    for i, z in enumerate(truss_zs()):
        for j, x in enumerate((-5.06, 5.06)):
            hero.append({"id": f"lantern_{i}{j}", "piece": "kit_lantern_hanging", "pos": [x, tie, z], "yaw": 0.0,
                         "args": {"drop": 0.4}})
    spec = {
        "budget": "classroom",
        "materials": {"castle_wall": "castle_wall_b", "castle_floor": "castle_floor_square"},
        "bay_target": 4.0,
        "runs": {"class_floor:n0": {"bounds": []}, "class_floor:s0": {"bounds": []},
                 "class_floor:w0": {"bounds": []}, "class_floor:e0": {"bounds": []}},
        "floor_border": True,
        "arches": [],
        "openings": [],
        "hero": hero,
        "replaces": [],
        "shade": {"radius": 2.5, "rays": 64},
    }
    lights = [
        _light("daylight_w", (-5.0, 6.5, 0.0), "sky", 7.0, 16.0),
        _light("daylight_e", (5.0, 6.5, 0.0), "sky", 7.0, 16.0),
        _light("plants", (0.0, 4.5, STAGING_Z), "sun", 5.0, 9.0),
        _light("lanterns_s", (0.0, 3.0, 3.0), "candle", 4.0, 10.0),
    ]
    views = {
        "entry": ((0.0, 2.6, 7.0), (0.0, 1.2, -6.0), 15),
        "front": ((-2.0, 1.8, -6.6), (2.0, 1.0, 6.0), 16),
        "side": ((8.6, 3.0, -6.8), (-5.0, 1.0, 4.0), 15),
        "plants": ((0.0, 1.7, -0.5), (0.0, 1.0, -4.0), 22),
        "benches": ((-7.4, 1.7, 6.2), (-4.25, 0.9, 1.8), 18),
        "up": ((0.0, 1.6, 5.0), (0.0, 7.0, -4.0), 12),
    }
    half = PORCH["width"] / 2.0
    head_door = {"run": "class_floor:s0", "t": 0.0, "piece": G.CLASS_DOOR, "span": [-half, half],
                 "top": PORCH["height"]}
    return KitRoom("class_greenhouse", CELL, NAME, _layout(), spec, [], lights,
                   {"sky": palette.GLOW["sky"], "ground": palette.CASTLE["flagstone"], "intensity": 0.8}, views,
                   head_door)


KIT_ROOMS = {"class_greenhouse": class_greenhouse}


def _plant(pid: str):
    def build():
        import greenhouse_plants

        return greenhouse_plants.objects(pid)

    return build


# The class plants (the animated props): each its own glb, standing at its landmark.
CLASS_PROPS = {"class_greenhouse": {pid: _plant(pid) for pid, _ in PLANTS}}
