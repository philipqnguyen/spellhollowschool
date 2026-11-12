"""The Creature Keeping class room,
`class_paddock`, built from castle-kit pieces like the other class rooms
(instance_kit.py; class_rooms.py merges this module's KIT_ROOMS and
CLASS_PROPS; docs/architecture.md). Pure Python, no `bpy` at
import time (the creatures' builders import it when Blender calls them).

An outdoor paddock open to the sky (Professor Tull's class, by day at
14:30 and by night at 01:00): the floor is a `grounds` surface with no
ceiling, so nothing roofs it and the client's sky shows overhead
(OPEN_SKY_LAYOUTS); its turf is `castle_floor_grass` (the grounds' grass
colour, world/palette.py CASTLE grass). The Planner walls it with the
grounds' low drystone wall (1 m) in the darker castle stone, and a
post-and-rail fence (`kit_fence_rail`) runs along the wall's top all round.
On the south edge a small stone gatehouse (the Astronomy tower's
`kit_stair_head`, reused) carries the class door
(instance_kit.KitRoom.head_door), lit by two wall lanterns. Inside: a low
railed pen round the two creatures (the props `beast_puff` and
`beast_lynxkit`, standing at the layout's landmarks of the same ids,
animated by the client), a water trough and hay bales, three rows of ten
straw standing marks (`kit_stand_mark`, flat: a full section of thirty
faces the pen and the professor). Outside the wall a clipped hedge and
round field trees close the view, and over the north-east corner the
keeper's stone hut (`kit_hut_corner`) shows its roof, chimney and a lit
window. Light: a high, warm sun and a cool sky light (static: the room is
lit the same at 14:30 and 01:00, only the client's sky dome follows the
hour), the gatehouse lanterns and the hut's window.

The contract's ids and spots: the floor `class_floor` (20 x 16 m), the
spawns `class_entry_a/b` at z 5.5, the exit `class_exit` at the
gatehouse's door (z 7.5), the professor's spot `class_professor` at
[0, 0, -6] facing +z (the students), the props' landmarks `beast_puff`
[-2, 0, -3] and `beast_lynxkit` [2, 0, -3].

Colours: every surface is a castle_* material (castle_textures.json) or a
props-atlas slot of a palette name; the creatures take palette names
(castle_textures.json `palette`) through a small swatch texture; light
and ambient colours come from `palette.py` (GLOW, CASTLE). No colour
literal here.
"""

from __future__ import annotations

import math

import palette
from castle_kit import grid as G
from castle_kit.pieces import paddock as PP  # noqa: F401  (registers the pieces' rules: class_paddock)
from instance_kit import KitRoom

X0, X1, Z0, Z1 = -10.0, 10.0, -8.0, 8.0
PROFESSOR = [0.0, 0.0, -6.0]
PI = round(math.pi, 7)
HALF_PI = round(math.pi / 2, 4)
CELL = "class_paddock"
NAME = "Creature Paddock"

BEASTS = {"beast_puff": ("Fluffbell", [-2.0, 0.0, -3.0]), "beast_lynxkit": ("Lynxkit", [2.0, 0.0, -3.0])}
LOW_WALL = 1.0  # layout_lib.OPEN_WALL_HEIGHT["grounds"]
WALL_MID = 0.25  # the low wall's centre line outside the floor (layout_lib.EXTERIOR_THICKNESS["grounds"] / 2)
GATEHOUSE = {"width": 6.0, "depth": 3.0, "height": 5.6}
FENCE = {"height": 0.95, "rails": 2}  # on the wall's top: 1.95 m over the grass
PEN = (-5.0, -5.0, 5.0, -1.2)  # x0, z0, x1, z1 of the creatures' pen
PEN_FENCE = {"height": 0.75, "rails": 2}  # low: the creatures show over and between its rails
END_CLEAR = 0.15  # a run meeting another's corner post stops this short
MARK_XS = tuple(round(-8.1 + 1.8 * i, 2) for i in range(10))
MARK_ZS = (0.4, 2.0, 3.6)
HUT = ([13.6, 0.0, -11.6], round(math.atan2(-13.6, 11.6), 4))  # its round side toward the paddock
TREES = (((-7.0, -12.6), 6.5), ((-1.0, -13.6), 7.4), ((5.0, -12.9), 6.0), ((-13.4, -3.2), 6.8), ((-14.0, 4.6), 5.6),
         ((13.6, 1.8), 6.2), ((13.0, 6.4), 5.4), ((-8.2, 12.6), 6.6), ((8.4, 12.9), 5.8))


def _layout() -> dict:
    return {
        "version": 1,
        "surfaces": [{"id": "class_floor", "kind": "grounds", "name": NAME, "level": 0, "cell": CELL, "min": [X0, Z0],
                      "max": [X1, Z1], "y": 0.0}],
        "spawns": [{"id": "class_entry_a", "pos": [-1.0, 0.0, 5.5], "yaw": PI},
                   {"id": "class_entry_b", "pos": [1.0, 0.0, 5.5], "yaw": PI}],
        "landmarks": [{"id": "class_professor", "name": "Professor's Desk", "pos": list(PROFESSOR)}]
        + [{"id": pid, "name": name, "pos": list(pos)} for pid, (name, pos) in BEASTS.items()],
        "interactables": [{"id": "class_exit", "kind": "instance_exit", "name": "Paddock Gate", "pos": [0.0, 0.0, 7.5],
                           "yaw": PI, "radius": 1.5}],
    }


def _light(lid: str, pos, colour: str, intensity: float, rng: float) -> dict:
    return {"id": lid, "kind": "point", "pos": [float(v) for v in pos], "color": colour, "intensity": intensity,
            "range": rng}


def _run(hid: str, start, yaw: float, length: float, y: float, args: dict) -> dict:
    """A fence run from `start` (x, z) along local +x turned by `yaw`."""
    return {"id": hid, "piece": "kit_fence_rail", "pos": [round(start[0], 4), y, round(start[1], 4)], "yaw": yaw,
            "args": {"length": round(length, 4), **args}}


def _fences() -> list[dict]:
    """The fence on the low wall's top all round (the south side either
    side of the gatehouse), and the creatures' pen."""
    c = WALL_MID
    xa, xb, za, zb = X0 - c, X1 + c, Z0 - c, Z1 + c
    head = GATEHOUSE["width"] / 2.0 + END_CLEAR
    out = [
        _run("fence_n", (xa, za), 0.0, xb - xa, LOW_WALL, FENCE),
        _run("fence_sw", (xa, zb), 0.0, -head - xa, LOW_WALL, FENCE),
        _run("fence_se", (head, zb), 0.0, xb - head, LOW_WALL, FENCE),
        _run("fence_w", (xa, zb - END_CLEAR), HALF_PI, (zb - za) - 2 * END_CLEAR, LOW_WALL, FENCE),
        _run("fence_e", (xb, zb - END_CLEAR), HALF_PI, (zb - za) - 2 * END_CLEAR, LOW_WALL, FENCE),
    ]
    px0, pz0, px1, pz1 = PEN
    out += [
        _run("pen_n", (px0, pz0), 0.0, px1 - px0, 0.0, PEN_FENCE),
        _run("pen_s", (px0, pz1), 0.0, px1 - px0, 0.0, PEN_FENCE),
        _run("pen_w", (px0, pz1 - END_CLEAR), HALF_PI, (pz1 - pz0) - 2 * END_CLEAR, 0.0, PEN_FENCE),
        _run("pen_e", (px1, pz1 - END_CLEAR), HALF_PI, (pz1 - pz0) - 2 * END_CLEAR, 0.0, PEN_FENCE),
    ]
    return out


def _hedges() -> list[dict]:
    """The hedge outside the wall: north (short of the hut), west, east
    (short of the hut) and south either side of the gatehouse."""
    off = 0.5 + 0.6  # the wall's thickness, then half the hedge's depth
    xa, xb, za, zb = X0 - off, X1 + off, Z0 - off, Z1 + off
    head = GATEHOUSE["width"] / 2.0 + 0.25
    runs = [
        ("hedge_n", (xa - 0.6, za), 0.0, 9.0 - (xa - 0.6), 1),
        ("hedge_w", (xa, zb + 0.6), HALF_PI, (zb + 0.6) - (za + 0.55), 2),
        ("hedge_e", (xb, zb + 0.6), HALF_PI, (zb + 0.6) - (-6.4), 3),
        ("hedge_sw", (xa - 0.6, zb), 0.0, -head - (xa - 0.6), 4),
        ("hedge_se", (head, zb), 0.0, xb + 0.6 - head, 5),
    ]
    return [{"id": hid, "piece": "kit_hedge_run", "pos": [round(s[0], 4), 0.0, round(s[1], 4)], "yaw": yaw,
             "args": {"length": round(length, 4), "height": 2.6, "depth": 1.1, "seed": seed}}
            for hid, s, yaw, length, seed in runs]


def class_paddock() -> KitRoom:
    hero = _fences()
    hero.append({"id": "gatehouse", "piece": G.STAIR_HEAD, "pos": [0.0, 0.0, Z1], "yaw": PI, "args": dict(GATEHOUSE)})
    for i, sx in enumerate((-1.0, 1.0)):
        hero.append({"id": f"lantern_door_{i}", "piece": "kit_lantern_wall", "pos": [sx * 2.3, 3.4, Z1], "yaw": PI})
    k = 0
    for z in MARK_ZS:
        for x in MARK_XS:
            hero.append({"id": f"mark_{k}", "piece": "kit_stand_mark", "pos": [x, 0.0, z], "yaw": 0.0, "args": {"seed": k + 1}})
            k += 1
    hero += [
        {"id": "trough", "piece": "kit_trough", "pos": [3.9, 0.0, -3.9], "yaw": HALF_PI},
        {"id": "hay_pen", "piece": "kit_hay_bale", "pos": [-3.9, 0.0, -4.2], "yaw": 0.35},
        {"id": "hay_w0", "piece": "kit_hay_bale", "pos": [-9.0, 0.0, -6.6], "yaw": HALF_PI},
        {"id": "hay_w1", "piece": "kit_hay_bale", "pos": [-9.0, 0.45, -6.4], "yaw": round(HALF_PI + 0.2, 4)},
        {"id": "hay_w2", "piece": "kit_hay_bale", "pos": [-8.4, 0.0, -5.4], "yaw": 0.1},
        {"id": "hay_e", "piece": "kit_hay_bale", "pos": [8.9, 0.0, 4.6], "yaw": HALF_PI},
        {"id": "crates", "piece": "kit_crate_stack", "pos": [-8.6, 0.0, 6.4], "yaw": HALF_PI, "args": {"seed": 0}},
        {"id": "hut", "piece": "kit_hut_corner", "pos": HUT[0], "yaw": HUT[1]},
    ]
    hero += _hedges()
    for i, ((x, z), h) in enumerate(TREES):
        hero.append({"id": f"tree_{i}", "piece": "kit_tree_round", "pos": [x, 0.0, z], "yaw": 0.0,
                     "args": {"height": h, "seed": i + 1}})
    spec = {
        "budget": "classroom",
        "materials": {"castle_wall": "castle_wall_c", "castle_floor": "castle_floor_grass"},
        "bay_target": 4.0,
        "runs": {"class_floor:n0": {"bounds": []}, "class_floor:s0": {"bounds": []},
                 "class_floor:w0": {"bounds": []}, "class_floor:e0": {"bounds": []}},
        "floor_border": False,
        "arches": [],
        "openings": [],
        "hero": hero,
        "replaces": [],
        "shade": {"radius": 2.5, "rays": 64},
    }
    lights = [
        _light("sun", (-6.0, 16.0, -3.0), palette.GLOW["sun"], 9.0, 36.0),
        _light("sky", (6.0, 14.0, 5.0), palette.GLOW["sky"], 6.0, 32.0),
        _light("gate_door", (0.0, 3.3, 6.9), palette.GLOW["candle"], 4.0, 9.0),
        _light("hut_window", (9.4, 2.6, -7.4), palette.GLOW["candle"], 3.0, 8.0),
    ]
    views = {
        "entry": ((0.0, 2.4, 6.6), (0.0, 1.0, -6.0), 15),
        "front": ((-1.5, 1.8, -6.6), (1.0, 1.2, 6.0), 16),
        "side": ((9.0, 3.8, -7.0), (-4.0, 0.8, 3.0), 15),
        "pen": ((0.0, 1.7, 1.2), (0.0, 0.3, -3.5), 24),
        "hut": ((-6.0, 1.7, 2.0), (12.0, 2.6, -10.0), 18),
        "high": ((0.0, 15.0, 19.0), (0.0, 0.0, -1.0), 20),
    }
    half = GATEHOUSE["width"] / 2.0
    head_door = {"run": "class_floor:s0", "t": 0.0, "piece": G.CLASS_DOOR, "span": [-half, half],
                 "top": GATEHOUSE["height"]}
    return KitRoom("class_paddock", CELL, NAME, _layout(), spec, [], lights,
                   {"sky": palette.GLOW["sky"], "ground": palette.CASTLE["grass"], "intensity": 0.9}, views,
                   head_door)


def _beast(pid: str):
    def build():
        import beast_props

        return beast_props.blender_objects(pid)

    build.__name__ = f"build_{pid}"
    return build


KIT_ROOMS = {"class_paddock": class_paddock}
CLASS_PROPS = {"class_paddock": {pid: _beast(pid) for pid in BEASTS}}
