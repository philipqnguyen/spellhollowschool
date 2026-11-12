"""Refit every garment shape and design onto a body .blend from any generator.

    blender -b --factory-startup --python tools/blender/characters/refit_garment_shapes.py -- \\
        --blend <body .blend> --out <dir> [--items gear/body/...,...] [--render]

The proof of docs/art/garment-shapes.md section 6: a new body only needs the
shapes refitted. The input is a saved body file (build_characters.py output,
e.g. art/characters/body_b_mh.blend from `--body-source makehuman`); the only
things read from it are what any rigged body has: its LOD0 mesh objects with
a `region` property, bone vertex groups and the build shape keys, and the
`<body>_skeleton` armature. No generator code runs, so this works on the
MakeHuman body (its own topology) exactly as on the current body.

Writes into <out>:

* `characters/<body>[_mh].blend`: the body with its old clothing removed and
  every design composed from its refitted shape (bound to the skeleton, so
  check_garment_shapes.py runs on it: `--art <out> --suffix _mh`);
* `gear/...glb` + `characters/manifest.json`: the per-item files, in the
  client's format;
* `refit_report.json`: the body frame's landmarks and per item the shape,
  fit time and triangle counts;
* with `--render`: `refit_<body>.png`, outfits on the refitted body (shared
  cel-shaded setup).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from types import SimpleNamespace

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import bpy  # noqa: E402

from charlib import designs as DS  # noqa: E402
from charlib import export as EX  # noqa: E402
from charlib import pipeline as PL  # noqa: E402


def args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--blend", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--items", default="")
    p.add_argument("--render", action="store_true")
    return p.parse_args(argv)


def load_body(path: str):
    bpy.ops.wm.open_mainfile(filepath=path)
    name = os.path.splitext(os.path.basename(path))[0]
    body_id = "_".join(name.split("_")[:2])  # body_a_mh, body_a_sculpt -> body_a
    skel = bpy.data.objects[f"{body_id}_skeleton"]
    # Drop the clothing the file was saved with (hair stays: it is not a shape).
    for o in [o for o in bpy.data.objects if o.get("asset") and str(o["asset"]).startswith("gear/") and not o.get("region")]:
        bpy.data.objects.remove(o)
    lod0 = {str(o["region"]): o for o in bpy.data.objects if o.type == "MESH" and o.get("region") and o.get("lod") == 0}
    mat = next(iter(lod0.values())).data.materials[0]
    for o in lod0.values():
        o.hide_set(False)
    # What the build knew about the body: its measurements (stored on the
    # skeleton), its generator and whether it was reshaped onto a sculpt
    # (stored by pipeline.build_body; older files: read from the file name).
    # A reshaped body fits hats on its eye-level head frame and clears fitted
    # gear off its skin at every build, exactly as build_characters.py does.
    from charlib import proportions as P

    rest = name[len(body_id):]
    source = str(skel.get("body_source") or ("makehuman" if rest.startswith(("_mh", "_sculpt")) else "current"))
    shape = skel.get("body_shape")
    shape = str(shape) if shape is not None else (rest.lstrip("_") if rest.startswith("_sculpt") else "")
    report = {"shape": {"target": shape}} if shape else {}
    return name, SimpleNamespace(id=body_id, body=P.of_skeleton(skel, body_id), meshes={0: lod0}, skel=skel,
                                 material=mat, source=source, report=report)


def render_sheet(bb, out_png: str) -> None:
    """A few outfits on the refitted body, front and three-quarter."""
    import check_characters as CC
    import render_garment_shapes as RG
    from charlib import render as RD
    from charlib import toon

    outfits = [o for o in RG.OUTFITS if o[0] in ("shirt_tie", "jumper", "robe_roarwing", "travel")] + RG.NEW_OUTFITS
    from charlib import atlas as A

    CC.pose(bb.skel, None, 0)
    # The saved file references the atlas by path; rebuild it in memory.
    old = bpy.data.images.get("character_atlas")
    if old is not None:
        bpy.data.images.remove(old)
    A.build_image()
    toon.studio((560, 760))
    items = {o: o.get("asset") for o in bpy.data.objects if o.type == "MESH" and o.get("asset") and not o.get("region")}
    regions = [o for o in bpy.data.objects if o.type == "MESH" and o.get("region")]
    for o in bpy.data.objects:
        if o.type == "ARMATURE" and o != bb.skel:
            o.hide_render = True
    h = max(v.co.z for v in bb.meshes[0]["scalp"].data.vertices) if "scalp" in bb.meshes[0] else 1.75
    paths = []
    for name, outfit, hair, house, skin, hc in outfits:
        dyes = RG.dyes_for(outfit, house)
        hat = any(x.startswith("gear/hat/") for x in outfit)
        styles = sorted({x for x in items.values() if x.startswith("hair/")})
        style = styles[hair % len(styles)] if styles else None
        hides = set()
        for o, asset in items.items():
            if asset.startswith("hair/"):
                on = asset == style and o.get("lod") == 0 and ("_cap" in o.name) == hat
                o.hide_render = not on
                if on:
                    hides |= set(o.get("hides", []))
                    toon.assign(o, toon.material(f"refit_hair_{hc}", RG.CREATOR["hair_colours"][hc]["hex"],
                                                 shadow_tint=toon.HAIR_SHADOW))
                continue
            on = asset in outfit and o.get("lod") == 0
            o.hide_render = not on
            if on:
                hides |= set(o.get("hides", []))
                d = dyes[asset]
                toon.assign(o, toon.material(f"refit_{'_'.join(d)}", *d))
        for o in regions:
            o.hide_render = o.get("lod") != 0 or o["region"] in hides
            toon.assign(o, toon.material(f"refit_skin_{skin}", RG.CREATOR["skin_tones"][skin]["hex"],
                                         RG.CREATOR["hair_colours"][hc]["hex"], shadow_tint=toon.SKIN_SHADOW))
        for az, el in ((0, 4), (35, 8)):
            RD.aim((0, 0, h * 0.52), az, el, 3.2, lens=50)
            paths.append(RD.shoot(os.path.join(os.path.dirname(out_png), f"_refit_{name}_{az}.png")))
    RD.sheet(paths, out_png, 2 * 4, scale=1)


def main():
    sys.path.append(os.path.dirname(HERE))  # tools/blender, for pins
    import pins  # tools/blender/BLENDER_VERSION

    pins.require_blender()
    a = args()
    out = os.path.abspath(a.out)
    name, bb = load_body(os.path.abspath(a.blend))
    t0 = time.time()
    fr = DS.frame_for(bb)
    shapes = DS.shapes_data()["shapes"]
    designs = DS.designs_data()["designs"]
    only = set(filter(None, a.items.split(",")))
    manifest = {"assets": {}}
    for asset, d in designs.items():
        if only and asset not in only:
            continue
        DS.build_item(bb, asset, d, shapes, out, manifest, None)
    for parts in [o for o in bpy.data.objects if o.get("asset") and str(o["asset"]).startswith("gear/")]:
        parts.hide_set(False)
        parts.hide_viewport = False
    os.makedirs(os.path.join(out, "characters"), exist_ok=True)
    EX.write_manifest(os.path.join(out, "characters", "manifest.json"), manifest)
    report = {"body": name, "frame": fr.summary(), "seconds": round(time.time() - t0, 1),
              "items": {k: v.get(bb.id) for k, v in DS.REPORT.items()}}
    with open(os.path.join(out, "refit_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, sort_keys=True)
        f.write("\n")
    PL.save_blend(os.path.join(out, "characters", f"{name}.blend"))
    PL.log(f"{name}: refitted {len(report['items'])} designs in {report['seconds']}s")
    if a.render:
        render_sheet(bb, os.path.join(out, f"refit_{name}.png"))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
