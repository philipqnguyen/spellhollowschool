"""Exports the two Wishing Rooms (`rooms_wishing_room.py`)
for the client, exactly as `build_school.py` exports the kitted class rooms
(the same glb, manifests and review renders), but in a step of their own, so
the class rooms' set and contract (`class_rooms.KIT_ROOMS`, the class tests)
stay as they are.

    blender -b --factory-startup --python tools/blender/world/build_wishing_room.py -- \\
        [--render] [--renders <dir>] [--blend-dir <dir>]

Writes `client/public/assets/instances/lost_property_vault.glb` and
`wishing_room_gathering.glb` (and the same files under `world/`), and merges their
entries into `instances/manifest.json`, `world/instances.json` and
`instances/school.json`. Run it after the castle textures (castle_kit/textures.py)
and the school step. No .blend unless `--blend-dir` names one.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import class_rooms  # noqa: E402
import rooms_wishing_room  # noqa: E402

# This process builds the Wishing Rooms only (build_school reads class_rooms.build_all()).
class_rooms.KIT_ROOMS = dict(rooms_wishing_room.KIT_ROOMS)
class_rooms.CLASS_PROPS = {}

import build_school  # noqa: E402

# The Gathering Room's rugs and cushions go in its manifest entry (the client's floor seats).
build_school.FLOOR_SEAT_ROOMS = set(rooms_wishing_room.KIT_ROOMS)

if "--" not in sys.argv:
    sys.argv.append("--")
_tail = sys.argv[sys.argv.index("--") + 1:]
if "--only" not in _tail:
    sys.argv += ["--only", ",".join(rooms_wishing_room.KIT_ROOMS)]
if "--blend-dir" not in _tail:
    sys.argv += ["--blend-dir", ""]

if __name__ == "__main__":
    build_school.main()
