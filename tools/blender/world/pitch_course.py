"""The Flying class's practice field, `class_pitch`
(docs/architecture.md). Pure Python, no `bpy`.

This script writes `data/world/instances/class_pitch.json` from
`contract_layout()` (`python3 -I tools/blender/world/pitch_course.py --write`;
`--check` fails when the file lacks or changes one of these entries). The room
module (school_rooms_pitch.py) builds the field's art round the same numbers and, once its room is in
`class_rooms.KIT_ROOMS`, `school_layouts.py --write` regenerates the file
from the room's `layout`, which must keep every id, spot, area and ring
below (it may add surfaces, landmarks and place areas).

The field: a 60 x 40 m turf floor open to the sky (`class_floor`, a
`grounds` surface with no ceiling; `build_school.OPEN_SKY_ROOMS`), walled
like the paddock, with the pitch's pieces round it (two hoops at each end, a
house-draped tower at each corner, a short stand behind the spawns). The
students arrive at the south end (`class_entry_a/b`) facing north; Madam
Swift stands in front of them (`class_professor`, facing them); the gate
out (`class_exit`) is behind them.

The airspace (`class_air`, `AreaKind::Airspace`) covers the floor inset by
1 m, from the turf (its floor: a broom flies `ground_clearance` over it) to
20 m. The ring course: eight rings (radius 2.5 m) on an ellipse 40 x 24 m
round the field's middle, at 5 to 9 m, each facing along the way round
(clockwise seen from above: north, east, south, west), about 13 m apart.
`BeatPrompt::Ring { ring }` is an index into `rings`.
"""

from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
PATH = os.path.join(REPO, "data", "world", "instances", "class_pitch.json")

LAYOUT_ID = "class_pitch"
CELL = "class_pitch"
NAME = "Practice Pitch"
FLOOR = (-30.0, -20.0, 30.0, 20.0)  # x0, z0, x1, z1 at y 0
PI = round(math.pi, 7)
SPAWNS = [("class_entry_a", [-1.0, 0.0, 16.0]), ("class_entry_b", [1.0, 0.0, 16.0])]
PROFESSOR = [0.0, 0.0, 10.0]  # facing +z (yaw 0): the students
EXIT = [0.0, 0.0, 18.5]
AIR = ([-29.0, 0.0, -19.0], [29.0, 20.0, 19.0])
RING_RADIUS = 2.5
ELLIPSE = (20.0, 12.0)  # half-widths along x and z
RING_HEIGHTS = [5.0, 7.0, 9.0, 7.0, 5.0, 7.0, 9.0, 7.0]


def rings() -> list[dict]:
    """Eight rings round the ellipse, clockwise from the north (seen from above)."""
    out = []
    ax, az = ELLIPSE
    for k, h in enumerate(RING_HEIGHTS):
        t = 2 * math.pi * k / len(RING_HEIGHTS)
        x, z = ax * math.sin(t), -az * math.cos(t)
        # Facing along the way round: the tangent (dx, dz); yaw 0 faces +z.
        yaw = math.atan2(ax * math.cos(t), az * math.sin(t))
        out.append({"id": f"ring_{k}", "pos": [round(x, 2) + 0.0, h, round(z, 2) + 0.0], "yaw": round(yaw, 4) + 0.0,
                    "radius": RING_RADIUS})
    return out


def contract_layout() -> dict:
    x0, z0, x1, z1 = FLOOR
    return {
        "version": 1,
        "surfaces": [{"id": "class_floor", "kind": "grounds", "name": NAME, "level": 0, "cell": CELL,
                      "min": [x0, z0], "max": [x1, z1], "y": 0.0}],
        "spawns": [{"id": sid, "pos": pos, "yaw": PI} for sid, pos in SPAWNS],
        "landmarks": [{"id": "class_professor", "name": "Madam Swift", "pos": PROFESSOR}],
        "interactables": [{"id": "class_exit", "kind": "instance_exit", "name": "Pitch Gate", "pos": EXIT,
                           "yaw": PI, "radius": 1.5}],
        "areas": [{"id": "class_air", "kind": "airspace", "name": "Over the practice pitch",
                   "min": AIR[0], "max": AIR[1]}],
        "rings": rings(),
    }


def missing(data: dict) -> list[str]:
    """The contract entries `data` (a parsed layout) lacks or changes: every
    spawn, landmark, interactable, area and ring of `contract_layout()` as it
    is, and `class_floor` covering the contract's floor at y 0."""
    want = contract_layout()
    out = []
    for key in ("spawns", "landmarks", "interactables", "areas", "rings"):
        have = {e["id"]: e for e in data.get(key, [])}
        for e in want[key]:
            if have.get(e["id"]) != e:
                out.append(f"{key}: {e['id']}")
    floor = next((s for s in data.get("surfaces", []) if s.get("id") == "class_floor"), None)
    x0, z0, x1, z1 = FLOOR
    if not floor or floor.get("y") != 0.0 or floor["min"][0] > x0 or floor["min"][1] > z0 \
            or floor["max"][0] < x1 or floor["max"][1] < z1:
        out.append("surfaces: class_floor")
    return out


def main() -> None:
    sys.path.insert(0, HERE)
    import school_layouts  # noqa: PLC0415

    text = school_layouts.to_json(contract_layout())
    if "--write" in sys.argv:
        with open(PATH, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {os.path.relpath(PATH, REPO)}")
    elif "--check" in sys.argv:
        with open(PATH, encoding="utf-8") as f:
            gone = missing(json.load(f))
        if gone:
            print(f"{os.path.relpath(PATH, REPO)} breaks the contract: " + ", ".join(gone))
            sys.exit(1)
        print("pitch_course: the contract holds")
    else:
        print(json.dumps(contract_layout(), indent=2))


if __name__ == "__main__":
    main()
