"""The pitch export hook greybox.py calls (docs/architecture.md;
the seam is world/outdoor.py). `build(ctx)` turns `pitch_build.build`'s
geometry into Blender objects and returns the resident cell `grid.P3B_PITCH_CELL`
(`pitch`): one mesh per castle_* material (`castle_floor_grass`, `castle_props`,
`castle_wood`, `castle_roof`, `castle_cloth`) with COLOR_0 as a plain grey, each
with its `<node>_lod1` (`extras.kit_lod`, drawn beyond grid.P3A_GROUNDS_LOD), and
the manifest's `seats` rows (the stands' benches, for the sit emote; each row's
tenth number whether it is two-sided, world/seat_sides.py).

It prints the lint's report (`pitch_build.lint`) and stops the export on an
error. No light: the pitch is lit by the sky and the moon.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import outdoor  # noqa: E402
import pitch_build as PB  # noqa: E402
import seat_sides  # noqa: E402
import solids  # noqa: E402
from castle_kit import grid as G  # noqa: E402


def build(ctx: outdoor.OutdoorContext) -> dict:
    meta = ctx.grounds["pitch"]
    pt = PB.build(ctx.layout, ctx.terrain_dir, ctx.textures, meta)
    errs, warns, rep = PB.lint(pt, ctx.layout, ctx.terrain_dir)
    print("pitch (Pitch):")
    for line in rep:
        print(f"  {line}")
    for w in warns:
        print(f"  warning: {w}")
    if errs:
        for e in errs:
            print(f"  error: {e}")
        raise SystemExit("pitch_export: lint failed, nothing exported")
    objs = list(ctx.kit_objects(pt.cell))  # the props kit of the cell, if any (none today)
    for name, md in sorted(pt.meshes.items()):
        objs.append(ctx.mesh_from_data(md, ctx.castle_material(md.material)))
    for name, (md, base) in sorted(pt.lod_meshes.items()):
        o = ctx.mesh_from_data(md, ctx.castle_material(md.material))
        o["kit_lod"] = {"base": base, "distance": float(G.P3A_GROUNDS_LOD)}
        objs.append(o)
    # The stands' benches are backless; each row's tenth number: two-sided (world/seat_sides.py).
    seats = seat_sides.Sides.from_file(solids.LAYOUT_PATH).mark(pt.seats)
    manifest = {"library": pt.library(), "kit": {"tris": pt.tris(), "draws": pt.draws()}, "lod": len(pt.lod_meshes),
                "seats": seats}
    return {pt.cell: outdoor.OutdoorCell(objs, pt.bounds(), manifest, [])}
