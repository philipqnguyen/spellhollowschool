"""Writes the instance layouts (data/world/instances/
class_charms.json and duel_arena.json) from the rooms in school_rooms.py,
and the kitted class rooms' (class_potions.json,
class_classroom.json) from class_rooms.py, so walkable ground always
matches the art (like dorm_layouts.py).

    blender -b --factory-startup --python tools/blender/world/school_layouts.py -- --write
    blender -b --factory-startup --python tools/blender/world/school_layouts.py -- --check

`--check` (used by the tests) fails if a file differs from what the rooms
generate, or if a layout breaks a rule of layout_lib (reachability, step
heights, spawns and interactables on walkable ground). The arena's data is
the 1d contract's and is reproduced byte for byte; the classroom adds its
tiers and gallery to the contract's ids (`class_floor`, `class_entry_a/b`,
`class_professor`, `class_exit`). No Blender API is used.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import class_rooms  # noqa: E402
import layout_lib  # noqa: E402
import school_rooms  # noqa: E402
import solids  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
INSTANCES_DIR = os.path.join(REPO, "data", "world", "instances")
# `areas` and `rings` (class_pitch's airspace and ring course, world/pitch_course.py).
# `solids`: the room's solid furniture (world/solids.py), measured off the room's own pieces.
KEYS = ("surfaces", "spawns", "landmarks", "interactables", "place_areas", "solids", "areas", "rings")


def to_json(data: dict) -> str:
    """The house style of the layout files: one object per line."""
    def one(o):
        return json.dumps(o, separators=(", ", ": "))

    lines = ["{", f'  "version": {data["version"]},']
    keys = [k for k in KEYS if k in data]
    for n, key in enumerate(keys):
        items = data[key]
        lines.append(f'  "{key}": [')
        for i, it in enumerate(items):
            lines.append("    " + one(it) + ("," if i < len(items) - 1 else ""))
        lines.append("  ]" + ("," if n < len(keys) - 1 else ""))
    lines.append("}")
    return "\n".join(lines) + "\n"


def with_solids(lid: str, room) -> dict:
    """The room's layout with its solid furniture (`solids`, world/solids.py)."""
    data = dict(room.layout)
    blockers, _ = solids.room_layout(lid, room)
    if blockers:
        data["solids"] = [b.to_json() for b in sorted(blockers, key=lambda b: b.id)]
    return data


def generate() -> dict[str, str]:
    """Layout id -> file text, for every room (the kitted class rooms too)."""
    out = {lid: to_json(with_solids(lid, room)) for lid, room in school_rooms.build_all().items()}
    out.update({lid: to_json(with_solids(lid, room)) for lid, room in class_rooms.build_all().items()})
    return dict(sorted(out.items()))


def main() -> None:
    from check_layout import script_args

    ap = argparse.ArgumentParser(description="Generate or check the class and arena instance layouts")
    ap.add_argument("--write", action="store_true", help="write data/world/instances/<id>.json")
    ap.add_argument("--check", action="store_true", help="fail if a file differs from the generated one")
    ap.add_argument("--out", default=INSTANCES_DIR)
    args = ap.parse_args(script_args(sys.argv))
    bad = 0
    for lid, text in generate().items():
        path = os.path.join(args.out, f"{lid}.json")
        layout = layout_lib.parse_layout(json.loads(text))
        rep = layout_lib.validate(layout)
        for e in rep.errors:
            print(f"{lid}: error: {e}")
        bad += len(rep.errors)
        if args.write:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"wrote {path} ({len(layout.surfaces)} surfaces)")
        elif args.check:
            current = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
            if current != text:
                print(f"{path} is out of date: run school_layouts.py --write")
                bad += 1
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
