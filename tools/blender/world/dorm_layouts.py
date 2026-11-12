"""Writes the dorm instance layouts (data/world/instances/dorm_<house>.json)
from the shells in dorms.py, so walkable ground and furniture areas always
match the art.

    blender -b --factory-startup --python tools/blender/world/dorm_layouts.py -- --write
    blender -b --factory-startup --python tools/blender/world/dorm_layouts.py -- --check

* Walkable surfaces: the room's inner outline inset by `WALK_MARGIN`, cut
  into horizontal bands (each band the widest rectangle the outline allows
  across it), minus the fixed furniture (bed, stove, fireplace) and wall
  pilasters, keeping only pieces connected to the spawn.
* Floor areas: the largest few rectangles inside the outline (inset by
  `PLACE_MARGIN`) clear of fixed furniture and the doorway; the largest is
  `floor`. Wall areas come with the shell (cardinal facets).
* Spawn just inside the door facing north; the `dorm_exit` at the door.

`--check` (used by the tests and CI) fails if a file differs from what the
shells generate. No Blender API is used: any Python 3.10+ with the tools
on its path can run it.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import dorms  # noqa: E402
import layout_lib  # noqa: E402
import meshkit as mk  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
INSTANCES_DIR = os.path.join(REPO, "data", "world", "instances")

WALK_MARGIN = 0.12
PLACE_MARGIN = 0.05
BANDS_PER_STEP = 2
MIN_SIDE = 0.35
MAX_FLOOR_AREAS = 6


def layout_ids() -> dict[str, str]:
    """House slot -> layout id (`dorm_<house id>`), from the palette's houses."""
    return {f"house_{chr(ord('a') + i)}": f"dorm_{h}" for i, h in enumerate(layout_lib.known_houses())}


def _inset(outline, d: float):
    """Insets a convex polygon (x, z) by d."""
    area = sum(outline[i][0] * outline[(i + 1) % len(outline)][1] - outline[(i + 1) % len(outline)][0] * outline[i][1]
               for i in range(len(outline)))
    pts = outline if area > 0 else list(reversed(outline))
    return mk.offset_convex(pts, -d)


def _x_span(poly, z: float):
    xs = []
    n = len(poly)
    for i in range(n):
        (xa, za), (xb, zb) = poly[i], poly[(i + 1) % n]
        if min(za, zb) - 1e-9 <= z <= max(za, zb) + 1e-9:
            if abs(zb - za) < 1e-9:
                xs += [xa, xb]
            else:
                xs.append(xa + (xb - xa) * (z - za) / (zb - za))
    return (min(xs), max(xs)) if xs else None


def bands(poly) -> list[tuple[float, float, float, float]]:
    """Horizontal bands covering a convex polygon: (x0, z0, x1, z1) each."""
    zs = sorted({round(p[1], 6) for p in poly})
    out = []
    for za, zb in zip(zs, zs[1:]):
        for k in range(BANDS_PER_STEP):
            z0 = za + (zb - za) * k / BANDS_PER_STEP
            z1 = za + (zb - za) * (k + 1) / BANDS_PER_STEP
            s0, s1 = _x_span(poly, z0 + 1e-7), _x_span(poly, z1 - 1e-7)
            if s0 and s1:
                x0, x1 = max(s0[0], s1[0]), min(s0[1], s1[1])
                if x1 - x0 > MIN_SIDE:
                    out.append((round(x0, 3), round(z0, 3), round(x1, 3), round(z1, 3)))
    return out


def _subtract_all(rects, holes, min_side: float):
    out = []
    for r in rects:
        out += layout_lib.rect_subtract(r, holes)
    return [tuple(round(v, 3) for v in r) for r in out if r[2] - r[0] >= min_side and r[3] - r[1] >= min_side]


def _touch(a, b) -> bool:
    """Two rectangles share an edge of positive length."""
    ox = min(a[2], b[2]) - max(a[0], b[0])
    oz = min(a[3], b[3]) - max(a[1], b[1])
    return (ox > 0.05 and abs(oz) < 1e-6) or (oz > 0.05 and abs(ox) < 1e-6)


def walkable(shell: dorms.Shell) -> list[tuple[float, float, float, float]]:
    # Narrow pieces stay: the union is what players walk on, and a strip
    # between a pilaster and a bed still joins its neighbours.
    rects = _subtract_all(bands(_inset(shell.outline, WALK_MARGIN)), shell.obstacles, 0.05)
    sx, _, sz, _ = shell.spawn
    start = next(i for i, r in enumerate(rects) if r[0] <= sx <= r[2] and r[1] <= sz <= r[3])
    seen, todo = {start}, [start]
    while todo:
        a = rects[todo.pop()]
        for j, b in enumerate(rects):
            if j not in seen and _touch(a, b):
                seen.add(j)
                todo.append(j)
    return [rects[i] for i in sorted(seen)]


def _inside_convex(poly, x: float, z: float) -> bool:
    n = len(poly)
    sign = 0.0
    for i in range(n):
        (xa, za), (xb, zb) = poly[i], poly[(i + 1) % n]
        c = (xb - xa) * (z - za) - (zb - za) * (x - xa)
        if abs(c) < 1e-9:
            continue
        if sign == 0.0:
            sign = c
        elif (c > 0) != (sign > 0):
            return False
    return True


def floor_areas(shell: dorms.Shell) -> list[tuple[float, float, float, float]]:
    """The largest rectangles inside the room clear of fixed furniture and
    the doorway: every rectangle whose sides lie on an outline vertex or an
    obstacle edge is tried, then the biggest are kept, skipping any inside
    one already kept."""
    poly = _inset(shell.outline, PLACE_MARGIN)
    holes = list(shell.obstacles) + list(shell.keep_clear)
    xs = sorted({round(v, 3) for v in [p[0] for p in poly] + [h[0] for h in holes] + [h[2] for h in holes]})
    zs = sorted({round(v, 3) for v in [p[1] for p in poly] + [h[1] for h in holes] + [h[3] for h in holes]})
    # Each band's edge also bounds rectangles across a round room.
    for b in bands(poly):
        xs = sorted(set(xs) | {b[0], b[2]})
        zs = sorted(set(zs) | {b[1], b[3]})
    cands = []
    for i, x0 in enumerate(xs):
        for x1 in xs[i + 1:]:
            if x1 - x0 < 1.0:
                continue
            for j, z0 in enumerate(zs):
                for z1 in zs[j + 1:]:
                    if z1 - z0 < 1.0:
                        continue
                    if not all(_inside_convex(poly, x, z) for x, z in ((x0, z0), (x1, z0), (x1, z1), (x0, z1))):
                        break  # taller rectangles from this z0 stick out further
                    if any(min(x1, h[2]) - max(x0, h[0]) > 1e-6 and min(z1, h[3]) - max(z0, h[1]) > 1e-6 for h in holes):
                        break
                    cands.append((x0, z0, x1, z1))
    cands.sort(key=lambda r: (-(r[2] - r[0]) * (r[3] - r[1]), r))
    kept = []
    for c in cands:
        if any(k[0] <= c[0] + 1e-6 and k[1] <= c[1] + 1e-6 and k[2] >= c[2] - 1e-6 and k[3] >= c[3] - 1e-6 for k in kept):
            continue
        # Skip near-duplicates of a kept area (mostly overlapping, little new floor).
        if any(_overlap(k, c) > 0.8 * (c[2] - c[0]) * (c[3] - c[1]) for k in kept):
            continue
        kept.append(c)
        if len(kept) >= MAX_FLOOR_AREAS:
            break
    return kept


def _overlap(a, b) -> float:
    ox = min(a[2], b[2]) - max(a[0], b[0])
    oz = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, ox) * max(0.0, oz)


def layout_for(shell: dorms.Shell) -> dict:
    surfaces = []
    for i, (x0, z0, x1, z1) in enumerate(walkable(shell)):
        surfaces.append({"id": "dorm_floor" if i == 0 else f"dorm_floor_{i + 1}", "kind": "room", "name": shell.name,
                         "level": 0, "cell": shell.cell, "min": [x0, z0], "max": [x1, z1], "y": 0,
                         "ceiling": round(shell.wall_h, 3)})
    sx, sy, sz, syaw = shell.spawn
    ex, ey, ez, er = shell.exit
    areas = []
    for k, (x0, z0, x1, z1) in enumerate(floor_areas(shell)):
        areas.append({"id": "floor" if k == 0 else f"floor_{k + 1}", "surface": "floor", "min": [x0, 0, z0], "max": [x1, 0, z1]})
    areas += [dict(a) for a in shell.wall_areas]
    areas += [dict(a) for a in shell.table_areas]
    return {
        "version": 1,
        "surfaces": surfaces,
        "spawns": [{"id": "dorm_entry", "pos": [sx, sy, round(sz, 3)], "yaw": round(syaw, 7)}],
        "interactables": [{"id": "dorm_exit", "kind": "dorm_exit", "name": shell.exit_name,
                           "pos": [ex, ey, round(ez, 3)], "yaw": round(math.pi, 7), "radius": er}],
        "place_areas": areas,
    }


def to_json(data: dict) -> str:
    """The house style of the layout files: one object per line."""
    def one(o):
        return json.dumps(o, separators=(", ", ": "))

    lines = ["{", f'  "version": {data["version"]},']
    keys = [k for k in ("surfaces", "spawns", "interactables", "place_areas") if k in data]
    for n, key in enumerate(keys):
        items = data[key]
        lines.append(f'  "{key}": [')
        for i, it in enumerate(items):
            lines.append("    " + one(it) + ("," if i < len(items) - 1 else ""))
        lines.append("  ]" + ("," if n < len(keys) - 1 else ""))
    lines.append("}")
    return "\n".join(lines) + "\n"


def generate() -> dict[str, str]:
    """Layout id -> file text, for every shell."""
    ids = layout_ids()
    return {ids[slot]: to_json(layout_for(shell)) for slot, shell in dorms.build_all().items()}


def main() -> None:
    from check_layout import script_args

    ap = argparse.ArgumentParser(description="Generate or check the dorm instance layouts")
    ap.add_argument("--write", action="store_true", help="write data/world/instances/*.json")
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
            print(f"wrote {path} ({len(layout.surfaces)} surfaces, {len(layout.place_areas)} place areas)")
        elif args.check:
            current = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
            if current != text:
                print(f"{path} is out of date: run dorm_layouts.py --write")
                bad += 1
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
