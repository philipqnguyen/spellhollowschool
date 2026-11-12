"""The landing rail, the one
prop every Grand Staircase gate is drawn from (grid.RAIL_PROP).

A gate is a stretch of the staircase's own stone balustrade
(`kit_balustrade_gothic`, the piece every flight and gallery edge carries)
between the newels on its two hinges: closed, the rail stands across the
landing edge; open, it is gone. The client conjures it in and out under a
puff of smoke (src/game/castle/stairs.ts): it never swings.

The prop is GATE_WIDTH long along +x from the origin (the first hinge), the
balustrade between the hinges' newels (rules.NEWEL_HALF in from each end),
centred on the gate line (z = 0; the coping's overhang on -z), standing on
y = 0. One mesh in `castle_trim` with its trim-sheet UVs and a COLOR_0
shade ramp; the material is a placeholder the client binds to the castle's
shared trim texture by name (castle/materials.ts), like a kitted cell's.

    blender -b --factory-startup --python tools/blender/world/stair_rail.py -- \\
        --out client/public/assets/instances/props

`rail_mesh()` is pure Python (tests/test_p3c_wings_a.py); the export runs
inside Blender and writes `<out>/stair_rail.glb`."""

from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLENDER = os.path.dirname(_HERE)
for _p in (_HERE, _BLENDER):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from castle_kit import assemble as A  # noqa: E402
from castle_kit import grid as G  # noqa: E402
from castle_kit import rules as R  # noqa: E402
from castle_kit import schema as S  # noqa: E402
from castle_kit.pieces import geom  # noqa: E402
from castle_kit.pieces import stairs as ST  # noqa: E402

MATERIAL = "castle_trim"
RAIL = 0.25  # the balustrade's depth (stairs.balustrade's `rail`)
# The shade (COLOR_0, the castle shader's value): a soft floor occlusion up the
# piers to the coping, near what the kitted cells' AO bake gives an edge balustrade.
SHADE_FOOT, SHADE_TOP, SHADE_UP = 0.62, 0.92, 0.06


class _Uv(A.Builder):
    """Just enough of the castle kit's Builder to map a piece's trim UVs."""

    def __init__(self, textures: dict):  # noqa: D401 - no layout needed
        self.textures = textures


def _shade(y: float, height: float, up: bool) -> float:
    t = min(1.0, max(0.0, y / height))
    t = t * t * (3.0 - 2.0 * t)
    return round(min(1.0, SHADE_FOOT + (SHADE_TOP - SHADE_FOOT) * t + (SHADE_UP if up else 0.0)), 4)


def rail_mesh(textures: dict | None = None) -> A.MeshData:
    """The rail: the balustrade between the two hinges' newels, centred on z = 0."""
    textures = textures or S.load_texture_set()
    inset = R.NEWEL_HALF
    piece = ST.balustrade(G.GATE_WIDTH - 2 * inset, rail=RAIL)
    faces = _Uv(textures).piece_faces(piece, geom.Placement((inset, 0.0, -RAIL / 2), 0.0), {}, G.RAIL_PROP)
    md = A.Builder._mesh(G.RAIL_PROP, MATERIAL, faces, (0.0, 1.0))
    height = piece.meta["height"]
    md.colors = [[(_shade(p[1], height, f.normal()[1] > 0.5),) * 3 for p in f.pts] for f in faces]
    return md


def main() -> None:
    import pins  # tools/blender/BLENDER_VERSION

    pins.require_blender()
    import bpyutil
    import greybox
    from castle_kit import visibility

    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(description="The landing rail prop -> <out>/stair_rail.glb")
    ap.add_argument("--out", default="client/public/assets/instances/props")
    args = ap.parse_args(argv)
    bpyutil.reset_scene()
    textures = S.load_texture_set()
    md = rail_mesh(textures)
    obj = greybox.mesh_from_data(md, greybox.castle_material(MATERIAL, textures))
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, f"{G.RAIL_PROP}.glb")
    greybox.export_cell([obj], path, extras=False)
    visibility.pack_color0(path)
    info = bpyutil.glb_info(path)
    print(f"{G.RAIL_PROP}: {info['triangles']} triangles, {info['draw_calls']} draw calls -> {path}")
    if info["triangles"] > G.RAIL_TRIS or info["draw_calls"] != 1:
        print(f"{G.RAIL_PROP}: over its budget ({G.RAIL_TRIS} triangles, 1 draw)")
        sys.exit(1)


if __name__ == "__main__":
    main()
