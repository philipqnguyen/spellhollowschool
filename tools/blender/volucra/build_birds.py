"""Exports Bird Conjuring's birds (birds_model.py) for the client:
`client/public/assets/volucra/birds.glb` (the nodes `bird_body`,
`bird_wing_l`, `bird_wing_r`, the wings children of the body at their
shoulders, and the conjured `bird_feather` on its own; one material on a
small swatch of palette names) and `manifest.json` (parts, triangles,
length, the feather).

    blender -b --factory-startup --python tools/blender/volucra/build_birds.py -- \\
        [--out client/public/assets/volucra]
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BLENDER = os.path.dirname(_HERE)
for _p in (_BLENDER, os.path.join(_BLENDER, "world"), _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import birds_model as bm  # noqa: E402

SWATCH_GRID = 2  # four zones
SWATCH_PX = 32


def zone_uv(zone: int) -> tuple[float, float]:
    import paint

    return paint.swatch_uv(sorted(bm.ZONES).index(zone), SWATCH_GRID)


def blender_objects(parts, conjured=None):
    """The bird as Blender objects: the body and its two wings (children),
    and the conjured feather (a root of its own)."""
    import bpyutil
    import paint
    from castle_kit import props_atlas

    names = props_atlas.palette()
    colours = [names[bm.ZONES[z]] for z in sorted(bm.ZONES)]
    img = bpyutil.image_from_array(f"{bm.PROP}_swatch", paint.swatches(SWATCH_PX, colours, SWATCH_GRID))
    mat = bpyutil.principled(bm.PROP, None, img, 0.85)
    tex = next(n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE")
    tex.interpolation = "Closest"
    objs = []
    for part in parts:
        uvs = [[zone_uv(f.zone)] * len(f.idx) for f in part.mesh.faces]
        o = bpyutil.mesh_object(part.name, part.mesh, {"fur": mat}, uvs=uvs)
        o.location = bpyutil.to_blender(part.origin)
        objs.append(o)
    for child in objs[1:]:
        child.parent = objs[0]
    if conjured is not None:
        uvs = [[zone_uv(f.zone)] * len(f.idx) for f in conjured.mesh.faces]
        o = bpyutil.mesh_object(conjured.name, conjured.mesh, {"fur": mat}, uvs=uvs)
        o.location = bpyutil.to_blender(conjured.origin)
        objs.append(o)
    return objs


def main() -> None:
    import pins  # tools/blender/BLENDER_VERSION

    pins.require_blender()
    import argparse

    import bpyutil
    from check_layout import script_args

    repo = os.path.abspath(os.path.join(_BLENDER, "..", ".."))
    ap = argparse.ArgumentParser(description="Bird Conjuring's birds (volucra/birds.glb)")
    ap.add_argument("--out", default=os.path.join(repo, "client", "public", "assets", "volucra"))
    args = ap.parse_args(script_args(sys.argv))
    os.makedirs(args.out, exist_ok=True)
    parts = bm.build()
    conjured = bm.feather()
    bpyutil.reset_scene()
    objs = blender_objects(parts, conjured)
    path = os.path.join(args.out, f"{bm.PROP}.glb")
    bpyutil.export_glb(objs, path)
    info = bpyutil.glb_info(path)
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({**bm.manifest(parts, conjured), "glb": f"{bm.PROP}.glb", "draw_calls": info["draw_calls"]}, f, indent=2)
        f.write("\n")
    print(f"{bm.PROP}: {info['triangles']} triangles, {info['draw_calls']} draw calls -> {path}")


if __name__ == "__main__":
    main()
