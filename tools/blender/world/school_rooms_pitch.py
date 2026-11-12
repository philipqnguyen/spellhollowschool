"""The Flying class's practice field, `class_pitch`, built from
castle-kit pieces like the other class rooms (instance_kit.py; class_rooms.py
merges this module's KIT_ROOMS and CLASS_PROPS; docs/architecture.md).
Pure Python, no `bpy` at import time (the ring prop's builder imports it when
Blender calls it).

Madam Swift's lesson, by day at 10:30 and 14:30, open to the sky: the floor is
the contract's 60 x 40 m `grounds` surface with no ceiling (OPEN_SKY_ROOMS),
its turf `castle_floor_grass`, walled by the grounds' low drystone wall (1 m)
in the castle stone. Round it, the Flying Pitch's own pieces: two gilt
hoops at each end (`kit_pitch_hoop`, outside the ring course), a house-draped
spectator tower at each corner beyond the wall (`kit_pitch_tower`, house_a..d),
a short covered stand with benches beyond the north wall facing the students
(`kit_pitch_stand`), and on the south edge a small stone gatehouse (the
Astronomy tower's `kit_stair_head`, as the paddock's) carrying the class door
(instance_kit.KitRoom.head_door), lit by two wall lanterns. Beyond the wall a
meadow of the same turf (`kit_pitch_apron`) runs out 70 m with round field
trees (`kit_tree_round`) on it, so a flyer 20 m up sees grass, not the void.

The contract's ids, spots, airspace and ring course are
world/pitch_course.py's (`contract_layout()`, kept byte for byte): the floor
`class_floor`, the spawns `class_entry_a/b` at z 16, Madam Swift's spot
`class_professor` at [0, 0, 10] facing +z (the students), the exit `class_exit`
"Pitch Gate" at [0, 0, 18.5], the airspace `class_air`, the rings
`ring_0`..`ring_7`. The rings themselves are drawn by the client with the
`ring_hoop` prop (CLASS_PROPS: one draw for all of them; it scales each to its
ring's radius and makes the next one glow).

Colours: every surface is a castle_* material (castle_textures.json) or a
props-atlas slot of a palette name; the ring prop's colour is the palette's
`gilt` (castle_textures.json `palette`); light and ambient colours come from
`palette.py` (GLOW, CASTLE). No colour literal here.
"""

from __future__ import annotations

import math

import palette
import pitch_course as PC
from castle_kit import grid as G
from castle_kit.pieces import paddock as _paddock  # noqa: F401  (the field trees' pieces)
from castle_kit.pieces import pitch as PP  # noqa: F401  (registers the room's rules: class_pitch)
from instance_kit import KitRoom

X0, Z0, X1, Z1 = PC.FLOOR
PI = round(math.pi, 7)
HALF_PI = round(math.pi / 2, 4)
CELL = PC.CELL
GATEHOUSE = {"width": 6.0, "depth": 3.0, "height": 5.6}
HOOPS = (((-27.0, -3.5), 8.0), ((-27.0, 3.5), 10.0), ((27.0, -3.5), 10.0), ((27.0, 3.5), 8.0))
HOOP_RADIUS = 1.3
TOWER = {"size": 4.0, "height": 16.0}
TOWERS = ((-34.5, -24.5, 0), (34.5, -24.5, 1), (-34.5, 24.5, 2), (34.5, 24.5, 3))
STAND = {"x0": -15.0, "front": -21.6, "length": 30.0, "depth": 4.0, "deck": 2.0, "roof": 3.4, "steps": 3.0}
BENCH_LEN, BENCH_PITCH, BENCH_ROWS = 4.8, 6.0, (-0.9, -2.2, -3.4)  # 1.2 m gaps: a way through from row to row
APRON = {"inner": 0.6, "outer": 70.0, "drop": 0.05}
TREES = (((-46.0, -34.0), 7.4), ((-12.0, -38.0), 6.6), ((22.0, -36.0), 7.8), ((48.0, -30.0), 6.4),
         ((-50.0, 2.0), 7.0), ((50.0, -4.0), 6.8), ((52.0, 14.0), 7.6), ((-47.0, 30.0), 6.2),
         ((-20.0, 34.0), 7.2), ((14.0, 35.0), 6.0), ((40.0, 33.0), 7.0), ((-60.0, -14.0), 7.8))
RING = {"radius": 1.0, "tube": 0.08, "segments": 32, "sides": 8}


def _layout() -> dict:
    return PC.contract_layout()


def _light(lid: str, pos, colour: str, intensity: float, rng: float) -> dict:
    return {"id": lid, "kind": "point", "pos": [float(v) for v in pos], "color": colour, "intensity": intensity,
            "range": rng}


def _hero() -> list[dict]:
    hero = [{"id": "gatehouse", "piece": G.STAIR_HEAD, "pos": [0.0, 0.0, Z1], "yaw": PI, "args": dict(GATEHOUSE)}]
    for i, sx in enumerate((-1.0, 1.0)):
        hero.append({"id": f"lantern_door_{i}", "piece": "kit_lantern_wall", "pos": [sx * 2.3, 3.4, Z1], "yaw": PI})
    for i, ((x, z), h) in enumerate(HOOPS):
        hero.append({"id": f"hoop_{i}", "piece": "kit_pitch_hoop", "pos": [x, 0.0, z], "yaw": HALF_PI,
                     "args": {"height": h, "radius": HOOP_RADIUS}})
    for i, (x, z, house) in enumerate(TOWERS):
        hero.append({"id": f"tower_{i}", "piece": "kit_pitch_tower", "pos": [x, 0.0, z], "yaw": 0.0,
                     "args": {"house": house, **TOWER, "slates": 0}})
    st = STAND
    hero.append({"id": "stand", "piece": "kit_pitch_stand", "pos": [st["x0"], 0.0, st["front"]], "yaw": 0.0,
                 "args": {"length": st["length"], "depth": st["depth"], "deck": st["deck"], "roof": st["roof"],
                          "steps": st["steps"], "steps_at": 1, "slates": 0}})
    n = int(st["length"] // BENCH_PITCH)
    gap = (st["length"] - n * BENCH_PITCH) / 2.0
    for r, bz in enumerate(BENCH_ROWS):
        for i in range(n):
            hero.append({"id": f"bench_{r}_{i}", "piece": f"kit_bench_{BENCH_LEN:g}",
                         "pos": [round(st["x0"] + gap + (i + 0.5) * BENCH_PITCH, 4), st["deck"], round(st["front"] + bz, 4)],
                         "yaw": 0.0})
    hero.append({"id": "apron", "piece": "kit_pitch_apron", "pos": [0.0, 0.0, 0.0], "yaw": 0.0,
                 "args": {"x0": X0, "z0": Z0, "x1": X1, "z1": Z1, **APRON}})
    for i, ((x, z), h) in enumerate(TREES):
        hero.append({"id": f"tree_{i}", "piece": "kit_tree_round", "pos": [x, -APRON["drop"], z], "yaw": 0.0,
                     "args": {"height": h, "seed": i + 3}})
    return hero


def class_pitch() -> KitRoom:
    spec = {
        "budget": "classroom",
        "materials": {"castle_wall": "castle_wall_c", "castle_floor": "castle_floor_grass"},
        "bay_target": 4.0,
        "runs": {"class_floor:n0": {"bounds": []}, "class_floor:s0": {"bounds": []},
                 "class_floor:w0": {"bounds": []}, "class_floor:e0": {"bounds": []}},
        "floor_border": False,
        "arches": [],
        "openings": [],
        "hero": _hero(),
        "replaces": [],
        "shade": {"radius": 2.5, "rays": 64},
    }
    lights = [
        _light("sun", (-10.0, 30.0, -6.0), palette.GLOW["sun"], 10.0, 70.0),
        _light("sky", (12.0, 28.0, 8.0), palette.GLOW["sky"], 7.0, 64.0),
        _light("gate_door", (0.0, 3.3, Z1 - 0.6), palette.GLOW["candle"], 4.0, 9.0),
    ]
    views = {
        "entry": ((0.0, 2.2, 18.0), (0.0, 4.0, -10.0), 16),
        "professor": ((-3.0, 1.8, 6.0), (1.0, 1.6, 17.0), 18),
        "air": ((-24.0, 16.0, 16.0), (6.0, 4.0, -6.0), 16),
        "high": ((0.0, 42.0, 52.0), (0.0, 0.0, 0.0), 18),
        "stand": ((0.0, 4.2, -24.0), (0.0, 5.0, 10.0), 16),
    }
    half = GATEHOUSE["width"] / 2.0
    head_door = {"run": "class_floor:s0", "t": 0.0, "piece": G.CLASS_DOOR, "span": [-half, half],
                 "top": GATEHOUSE["height"]}
    return KitRoom("class_pitch", CELL, PC.NAME, _layout(), spec, [], lights,
                   {"sky": palette.GLOW["sky"], "ground": palette.CASTLE["grass"], "intensity": 0.9}, views, head_door)


def ring_mesh():
    """The `ring_hoop` prop's mesh: a ring RING radius round its centre (the
    origin) in the local xy plane, facing +z; one material."""
    import meshkit as mk

    m = mk.Mesh()
    r, t, seg, sides = RING["radius"], RING["tube"], RING["segments"], RING["sides"]
    g = m.group()
    for i in range(seg):
        a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
        for j in range(sides):
            b0, b1 = 2 * math.pi * j / sides, 2 * math.pi * (j + 1) / sides

            def p(a, b):
                rr = r + t * math.cos(b)
                return (rr * math.cos(a), rr * math.sin(a), t * math.sin(b))

            m.poly([p(a0, b0), p(a1, b0), p(a1, b1), p(a0, b1)], "metal", zone=0, group=g)
    return m


def build_ring_hoop():
    """The ring prop as Blender objects (build_school.build_prop): one mesh, one
    plain material in the palette's `gilt`. Runs inside Blender."""
    import bpyutil
    from castle_kit import props_atlas

    mat = bpyutil.principled("ring_hoop", props_atlas.palette()["gilt"], None, 0.45)
    return [bpyutil.mesh_object("ring_hoop", ring_mesh(), {"metal": mat})]


KIT_ROOMS = {"class_pitch": class_pitch}
CLASS_PROPS = {"class_pitch": {"ring_hoop": build_ring_hoop}}
