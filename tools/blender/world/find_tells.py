"""Writes the find-spot tells into data/world/props.json: for every
`find_spot` of the castle layout, one small `tell_*` kit entry next to it
(kit_models.py), so attentive players can spot where something is hidden.

    blender -b --factory-startup --python tools/blender/world/find_tells.py -- --write
    blender -b --factory-startup --python tools/blender/world/find_tells.py -- --check

Which tell, and where:

* spots on the grounds or in the courtyard: a dark rock with a glint at
  its foot, on the spot;
* spots named for a tapestry: a folded-up tapestry corner on the nearest
  wall;
* spots by armour, barrels, shelves, hearths or fireplaces (already full
  of dressing): a glint in the air low over the spot;
* everywhere else: a loose stone in the nearest wall within reach (a
  glint when no wall is near).

The nearest wall is an edge of the surface the spot stands on that no
other surface continues past (a doorway or an adjoining floor is not a
wall). Tells sit on that wall at the point nearest the spot, facing into
the room. Entries whose id starts with `tell_` are regenerated; the rest of
the file is kept as written. `--check` (used by the tests) fails when the
file differs from what the layout generates. No Blender API is used.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import layout_lib  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
LAYOUT = os.path.join(REPO, "data", "world", "layout.json")
PROPS = os.path.join(REPO, "data", "world", "props.json")

WALL_REACH = 2.2  # metres: farther than this from any wall, the tell is a glint
OUTDOOR = ("grounds", "courtyard")
GLINT_WORDS = ("armour", "suit", "barrel", "shelf", "hearth", "fireplace", "bust", "portrait")


def _open_beyond(layout, s, x: float, z: float, nx: float, nz: float) -> bool:
    """Does another walkable surface continue past surface `s` at (x, z),
    stepping out along (nx, nz)?"""
    px, pz = x + nx * 0.15, z + nz * 0.15
    y = s.height(x, z)
    for o in layout.surfaces:
        if o is s or not o.contains(px, pz, 0.02):
            continue
        if abs(o.height(px, pz) - y) < 1.0:
            return True
    return False


def nearest_wall(layout, s, x: float, z: float):
    """(distance, wall point x, z, yaw facing into the room) of the nearest
    walled edge of `s`, or None."""
    edges = [
        (x - s.x0, s.x0, z, -1.0, 0.0, math.pi / 2),   # west wall: tell faces +X
        (s.x1 - x, s.x1, z, 1.0, 0.0, -math.pi / 2),   # east wall: faces -X
        (z - s.z0, x, s.z0, 0.0, -1.0, 0.0),           # north wall: faces +Z
        (s.z1 - z, x, s.z1, 0.0, 1.0, math.pi),        # south wall: faces -Z
    ]
    best = None
    for dist, wx, wz, nx, nz, yaw in edges:
        if _open_beyond(layout, s, wx, wz, nx, nz):
            continue
        if best is None or dist < best[0]:
            best = (dist, wx - nx * 0.002, wz - nz * 0.002, yaw)
    return best


def tell_for(layout, spot) -> dict:
    s = layout_lib.interactable_surface(layout, spot)
    if s is None:
        raise SystemExit(f"find spot {spot.id} stands on no surface")
    x, y, z = spot.pos
    name = spot.id.lower()
    entry = {"id": f"tell_{spot.id[len('find_'):] if spot.id.startswith('find_') else spot.id}", "cell": s.cell}
    if s.kind in OUTDOOR:
        entry.update(model="tell_rock_glint", pos=[x + 0.35, s.height(x + 0.35, z), z + 0.2], yaw=0.6)
        return entry
    wall = None if any(w in name for w in GLINT_WORDS) else nearest_wall(layout, s, x, z)
    if wall is not None and wall[0] <= WALL_REACH:
        _, wx, wz, yaw = wall
        model = "tell_tapestry_corner" if "tapestry" in name else "tell_loose_stone"
        entry.update(model=model, pos=[round(wx, 3), round(s.height(wx, wz), 3), round(wz, 3)], yaw=round(yaw, 4))
        return entry
    entry.update(model="tell_glint", pos=[x, s.height(x, z), z], yaw=0.0)
    return entry


def generate(props_text: str | None = None) -> str:
    """props.json with its `kit` list's tells regenerated (the rest of the
    file is kept byte for byte)."""
    layout = layout_lib.load_layout(LAYOUT)
    if props_text is None:
        with open(PROPS, encoding="utf-8") as f:
            props_text = f.read()
    data = json.loads(props_text)
    kept = [k for k in data.get("kit", []) if not k["id"].startswith("tell_")]
    tells = [tell_for(layout, it) for it in layout.interactables if it.kind == "find_spot"]
    for t in tells:
        t["pos"] = [round(v, 3) for v in t["pos"]]
    return replace_kit(props_text, kept, tells)


def _entry(e: dict) -> str:
    """One entry in the file's house style: `{ "id": ..., "cell": ... }`."""
    return "{ " + ", ".join(f'"{k}": {json.dumps(v)}' for k, v in e.items()) + " }"


def replace_kit(text: str, kept: list[dict], tells: list[dict]) -> str:
    groups = [g for g in (kept, tells) if g]
    body = ",\n\n".join(",\n".join("    " + _entry(e) for e in g) for g in groups)
    block = '  "kit": [\n' + body + "\n  ]"
    m = re.search(r'\n  "kit": \[\n.*?\n  \]', text, re.S)
    if m:
        return text[: m.start()] + "\n" + block + text[m.end():]
    head = text[: text.rstrip().rfind("}")].rstrip()
    return head + ",\n\n" + block + "\n}\n"


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    ap = argparse.ArgumentParser(description="Generate the find-spot tells in props.json")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    text = generate()
    with open(PROPS, encoding="utf-8") as f:
        current = f.read()
    if args.write:
        if text != current:
            with open(PROPS, "w", encoding="utf-8") as f:
                f.write(text)
        print(f"wrote {PROPS}")
    elif text != current:
        print(f"{PROPS} is out of date: run tools/blender/world/find_tells.py -- --write")
        sys.exit(1)
    else:
        print(f"{PROPS} is up to date")


if __name__ == "__main__":
    main()
