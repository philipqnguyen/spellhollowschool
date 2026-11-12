"""The Astronomy class room, `class_astronomy`, built
from castle-kit pieces like the rooms (instance_kit.py,
class_rooms.py, which merges this module's KIT_ROOMS; docs/architecture.md).
Pure Python, no `bpy`.

A tower-top platform open to the night sky (Professor Arden's night
class): the floor is a `grounds` surface with no ceiling, so nothing roofs
it and the client's shared sky shows overhead; the Planner walls it with the
grounds' low wall (1 m, the breast), on which the M5c crenellated parapet
(`kit_parapet_crenel`) runs all round, embrasure sills at 1.15 m and merlons
to 2.5 m: nobody can step off (the floor ends at the wall's inner face) and
the void round the instance stays below the battlements. On the south edge
the head of the tower stair (`kit_stair_head`, its own parapet on top)
carries the class door (instance_kit.KitRoom.head_door), lit by two wall
lanterns. Fifteen brass telescopes on oak tripods (`kit_telescope`, one per
pair of students) stand in three rows of five aimed north, each with a
study chair either side (thirty seats, facing the professor); the professor
has a larger telescope and a desk by the north parapet. The platform is
moonlit: a high, cool, low moonlight (`palette.GLOW` sky) and a cool fill;
the only warm light is the stair door's pair of lanterns, kept to the door
(the 2c fix: the two lamp posts in the north corners lit the whole
platform warm, floodlit rather than moonlit, so they went).

The contract's ids and spots: the floor `class_floor` (20 x 16 m), the
spawns `class_entry_a/b` at z 5.5, the exit `class_exit` at the stair head's
door (z 7.5), the professor's spot `class_professor` at [0, 0, -6] facing
+z (the students).

Colours: every surface is a castle_* material (castle_textures.json) or a
props-atlas slot of a palette name; light and ambient colours come from
`palette.py` (GLOW, CASTLE). No colour literal here.
"""

from __future__ import annotations

import json
import math
import os

import layout_lib as L
import palette
from castle_kit import grid as G
from instance_kit import KitRoom

X0, X1, Z0, Z1 = -10.0, 10.0, -8.0, 8.0
PROFESSOR = [0.0, 0.0, -6.0]
PI = round(math.pi, 7)
HALF_PI = round(math.pi / 2, 4)
CELL = "class_astronomy"
NAME = "Astronomy Tower"
LIGHTING = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "data", "art", "lighting.json")

LOW_WALL = L.OPEN_WALL_HEIGHT["grounds"]  # the grounds' low wall: the parapet's breast
WALL_THICK = L.EXTERIOR_THICKNESS["grounds"]
STAIR_HEAD = {"width": 6.0, "depth": 3.5, "height": 6.4}
TELESCOPE_XS = (-7.6, -3.8, 0.0, 3.8, 7.6)
TELESCOPE_ZS = (-2.6, 0.5, 3.6)
CHAIR_SIDE = 0.85  # a chair's centre either side of its telescope
CHAIR_BEHIND = 0.55  # and behind it (south): the pair sits facing the professor, the telescope between them
TELESCOPE_TILT = 0.6
# The parapets' copings oversail 0.06 m: a run meeting another's back stops two overhangs short
# (as the facades' corners, rules.PARAPET_CORNER_CLEAR), one meeting the stair head's side one and a bit.
CORNER_CLEAR = 0.13
COPING_CLEAR = 0.07
# Moonlit, not floodlit. The moon a high, cool, lower light; the stair
# door's warm light reaches the door's few metres only; the room's fill a little dimmer.
MOON = (0.0, 14.0, 1.0)
MOON_INTENSITY = 8.0
MOON_RANGE = 30.0
DOOR_INTENSITY = 2.5
DOOR_RANGE = 6.0
FILL = 0.3


def _layout() -> dict:
    return {
        "version": 1,
        "surfaces": [{"id": "class_floor", "kind": "grounds", "name": NAME, "level": 0, "cell": CELL, "min": [X0, Z0],
                      "max": [X1, Z1], "y": 0.0}],
        "spawns": [{"id": "class_entry_a", "pos": [-1.0, 0.0, 5.5], "yaw": PI},
                   {"id": "class_entry_b", "pos": [1.0, 0.0, 5.5], "yaw": PI}],
        "landmarks": [{"id": "class_professor", "name": "Professor's Desk", "pos": list(PROFESSOR)}],
        "interactables": [{"id": "class_exit", "kind": "instance_exit", "name": "Tower Stair", "pos": [0.0, 0.0, 7.5],
                           "yaw": PI, "radius": 1.5}],
    }


def _light(lid: str, pos, colour: str, intensity: float, rng: float) -> dict:
    return {"id": lid, "kind": "point", "pos": [float(v) for v in pos], "color": colour, "intensity": intensity,
            "range": rng}


def _parapets() -> list[dict]:
    """The crenellated parapet on the low wall's top, fronts outward: the
    north wall's whole length over both corners, the south wall's either
    side of the stair head, the east and west walls between their backs."""
    pp = G.PLATFORM_PARAPET
    out_x, out_z = X1 + WALL_THICK, Z1 + WALL_THICK  # the low wall's outer faces
    back = pp["thick"] - pp["proud"]  # a parapet's back lies this far inside the outer face
    ends = out_x + pp["proud"]
    head = STAIR_HEAD["width"] / 2.0 + COPING_CLEAR  # the south runs stop short of the stair head
    side = out_z - back - CORNER_CLEAR  # the east and west runs stop short of the north and south runs
    runs = [
        ("parapet_n", [ends, LOW_WALL, -out_z], PI, 2 * ends),
        ("parapet_sw", [-ends, LOW_WALL, out_z], 0.0, ends - head),
        ("parapet_se", [head, LOW_WALL, out_z], 0.0, ends - head),
        ("parapet_e", [out_x, LOW_WALL, side], HALF_PI, 2 * side),
        ("parapet_w", [-out_x, LOW_WALL, -side], -HALF_PI, 2 * side),
    ]
    return [{"id": hid, "piece": G.PARAPET_CRENEL, "pos": [round(v, 4) for v in pos], "yaw": yaw,
             "args": {"length": round(length, 4), **pp}} for hid, pos, yaw, length in runs]


def class_astronomy() -> KitRoom:
    hero = _parapets()
    hero.append({"id": "stair_head", "piece": G.STAIR_HEAD, "pos": [0.0, 0.0, Z1], "yaw": PI, "args": dict(STAIR_HEAD)})
    for i, sx in enumerate((-1.0, 1.0)):
        hero.append({"id": f"lantern_door_{i}", "piece": "kit_lantern_wall", "pos": [sx * 2.3, 3.4, Z1], "yaw": PI})
    k = 0
    for z in TELESCOPE_ZS:
        for x in TELESCOPE_XS:
            hero.append({"id": f"telescope_{k}", "piece": G.TELESCOPE, "pos": [x, 0.0, z], "yaw": PI,
                         "args": {"seed": k + 1, "tilt": TELESCOPE_TILT}})
            for j, sx in enumerate((-1.0, 1.0)):
                hero.append({"id": f"chair_{k}_{j}", "piece": "kit_chair_study",
                             "pos": [round(x + sx * CHAIR_SIDE, 4), 0.0, round(z + CHAIR_BEHIND, 4)], "yaw": PI})
            k += 1
    hero += [
        {"id": "telescope_professor", "piece": G.TELESCOPE, "pos": [-3.4, 0.0, -6.7], "yaw": round(math.pi - 0.45, 4),
         "args": {"seed": 40, "scale": 1.5, "tilt": 0.85}},
    ]
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
    kit = [{"id": "professor_desk", "model": "teacher_desk", "pos": [3.4, 0.0, -6.9], "yaw": 0.0}]
    lights = [
        _light("moon", (MOON[0], MOON[1], MOON[2]), palette.GLOW["sky"], MOON_INTENSITY, MOON_RANGE),
        _light("stair_door", (0.0, 3.3, 6.9), palette.GLOW["candle"], DOOR_INTENSITY, DOOR_RANGE),
    ]
    views = {
        "entry": ((0.0, 2.4, 6.6), (0.0, 1.4, -6.0), 15),
        "front": ((-1.5, 1.8, -6.6), (1.0, 2.0, 6.0), 16),
        "side": ((9.0, 3.8, -7.0), (-4.0, 0.8, 3.0), 15),
        "telescopes": ((-5.6, 1.5, 5.6), (-1.0, 1.0, -1.0), 18),
        "high": ((0.0, 15.0, 19.0), (0.0, 0.0, -1.0), 20),
        "up": ((0.0, 1.7, 6.6), (0.0, 9.0, -8.0), 12),
    }
    half = STAIR_HEAD["width"] / 2.0
    head_door = {"run": "class_floor:s0", "t": 0.0, "piece": G.CLASS_DOOR, "span": [-half, half],
                 "top": STAIR_HEAD["height"]}
    return KitRoom("class_astronomy", CELL, NAME, _layout(), spec, kit, lights,
                   {"sky": palette.GLOW["sky"], "ground": palette.CASTLE["stone_dark"], "intensity": FILL}, views,
                   head_door, (night_zenith(), 2.0))


def night_zenith() -> str:
    """The night sky's zenith (data/art/lighting.json, the castle's `night`
    key): the review renders' world, as the client's sky shows it."""
    with open(LIGHTING, encoding="utf-8") as f:
        keys = json.load(f)["castle"]["keys"]
    return next(k for k in keys if k["name"] == "night")["sky"]["zenith"]


KIT_ROOMS = {"class_astronomy": class_astronomy}
