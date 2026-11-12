"""`class_defence`, the Defensive Magic
classroom (docs/architecture.md). Pure Python, no `bpy`.

The shared classroom shell of `class_classroom` (class_rooms.py: the pale
castle stone, the barrel vault on three ribs, four lancets east, the wall
and hanging lanterns, the class door in the south wall, the blackboard)
with a drill dressing instead of Transfiguration's desks: the floor
cleared for the drills, the drill ring (`kit_drill_ring`, after the
Dueling Club stage's ring and star) let into the floor round the
professor's spot, six practice mats (`kit_rug_4x3` in the neutral field)
where the students stand in two rows facing the professor, four practice
targets (`kit_practice_target`) along the side walls, the professor's
table by the north-east bookcase
and an old cupboard in the south-west corner. The Transfiguration dressing
is unchanged (it is the same shell built under another cell id).

Contract ids and spots as every class room: the floor `class_floor`, the
spawns `class_entry_a/b`, the exit `class_exit` at the south wall's class
door, the professor's spot `class_professor` at [0, 0, -6]. Colours: castle_*
materials and props-atlas palette names only; light colours from
`palette.py`. No colour literal here."""

from __future__ import annotations

import copy
import math

PI = round(math.pi, 7)
HALF_PI = round(math.pi / 2, 4)

# The shell's dressing that goes (Transfiguration's desks, chairs and desk things, the
# practice table) and what stays (the vault, the lanterns, four bookcases, the blackboard).
DROP_HERO = ("desk_", "chair_", "professor_table", "bookcase_w2", "rug_front")
DROP_KIT = ("practice_table",)

MAT_XS = (-6.0, 0.0, 6.0)
MAT_ZS = (-1.6, 2.4)
RING_CENTRE = (0.0, 0.0, -5.8)
RING_R = 1.5


def class_defence():
    import class_rooms  # here, not at the top: class_rooms imports this module when it loads
    from instance_kit import KitRoom

    shell = class_rooms.class_classroom()
    cell = "class_defence"
    spec = copy.deepcopy(shell.spec)
    spec["hero"] = [h for h in spec["hero"] if not h["id"].startswith(DROP_HERO)]
    hero = spec["hero"]
    hero.append({"id": "drill_ring", "piece": "kit_drill_ring", "pos": list(RING_CENTRE), "yaw": 0.0,
                 "args": {"radius": RING_R}})
    k = 0
    for z in MAT_ZS:
        for x in MAT_XS:
            hero.append({"id": f"mat_{k}", "piece": "kit_rug_4x3", "pos": [x, 0.0, z], "yaw": 0.0, "args": {"field": 4}})
            k += 1
    for i, (x, z, yaw) in enumerate(((-9.0, -1.8, HALF_PI), (-9.0, 2.2, HALF_PI), (9.0, -2.0, -HALF_PI),
                                     (9.0, 2.0, -HALF_PI))):
        hero.append({"id": f"target_{i}", "piece": "kit_practice_target", "pos": [x, 0.0, z], "yaw": yaw})
    hero += [
        {"id": "professor_table", "piece": "kit_table_study", "pos": [6.4, 0.0, -5.6], "yaw": 0.0, "args": {"length": 1.6}},
        {"id": "professor_things", "piece": "kit_desk_objects", "pos": [6.4, class_rooms.TABLE_TOP, -5.6], "yaw": PI,
         "args": {"seed": 5}},
    ]
    kit = [dict(e) for e in shell.kit if e["id"] not in DROP_KIT]
    kit.append({"id": "cupboard", "model": "apothecary_cupboard", "pos": [-7.4, 0.0, 7.95], "yaw": PI})
    layout = copy.deepcopy(shell.layout)
    layout["surfaces"][0].update(cell=cell, name="Defence Classroom")
    views = {
        "entry": ((0.0, 2.6, 7.0), (0.0, 1.2, -6.0), 15),
        "front": ((-2.0, 1.8, -6.6), (2.0, 1.0, 6.0), 16),
        "side": ((8.6, 3.6, -6.8), (-5.0, 1.0, 4.0), 15),
        "drill": ((-6.5, 1.7, 5.2), (1.0, 1.0, -4.5), 16),
        "targets": ((5.0, 1.6, 4.5), (9.0, 1.4, -2.0), 22),
    }
    return KitRoom("class_defence", cell, "Defence Classroom", layout, spec, kit, [dict(lt) for lt in shell.lights],
                   dict(shell.ambient), views)


KIT_ROOMS = {"class_defence": class_defence}
