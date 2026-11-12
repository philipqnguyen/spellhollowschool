"""Stills of the castle's far silhouette for review
(docs/art/castle-kit.md "Exterior"): from the lake shore, the
boathouse's door and just outside the courtyard gate, by day and by night.

    blender -b --factory-startup --python tools/blender/world/hlod_stills.py -- \\
        [--assets client/public/assets] [--out .out/renders] [--views lake_shore,boathouse,gate] \\
        [--hours 13,23]

Writes `<out>/p3a-<view>-<day|night>.png` (1600 x 900). The scene is what the
client would stream at that spot: the castle cells within the load radius
(70 m, Medium) of the eye, every `resident` cell (the grounds, the lake, the
backdrop and foliage when they exist), and `hlod.glb` with a chunk hidden
when every one of its cells is loaded (the client's rule). Lit and toon-shaded
by world/render_toon.py (the castle keys of data/art/lighting.json at the
hour), with no outline on the castle. Not the client's renderer: the
client's view-distance fog is left out so the skyline can be judged."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import bpy  # noqa: E402

import render_toon as RT  # noqa: E402
from check_layout import script_args  # noqa: E402

LOAD_RADIUS = 70.0  # the client's load radius (Medium and High)

# name -> (eye, target, lens mm): docs/art/castle-kit.md "Exterior", eyes 1.6 m over the ground.
VIEWS = {
    "lake_shore": ((5.0, -13.2, 251.0), (-4.0, 58.0, 10.0), 28),
    "boathouse": ((-92.0, -12.7, 202.0), (0.0, 55.0, 10.0), 24),
    "gate": ((16.0, 1.6, 100.0), (16.0, 12.5, 90.0), 16),
    # (Not in 34.7: the courtyard's sky with the silhouette round it, a regression check.)
    "courtyard": ((34.0, 1.6, 84.0), (-6.0, 26.0, 44.0), 18),
    # (the playtest's spots and the silhouette from every side.)
    "paddock_path": ((72.0, -3.1, 160.0), (-10.0, 30.0, 20.0), 24),
    "owl_loft_knoll": ((-84.0, 5.8, 124.0), (-20.0, 30.0, 20.0), 24),
    "south_far": ((10.0, 40.0, 360.0), (10.0, 25.0, 20.0), 35),
    "north_far": ((10.0, 40.0, -320.0), (10.0, 25.0, 20.0), 35),
    "east_far": ((340.0, 40.0, 20.0), (0.0, 25.0, 20.0), 35),
    "west_far": ((-330.0, 40.0, 20.0), (0.0, 25.0, 20.0), 35),
    "wall_west": ((14.0, 2.2, 96.5), (-18.0, 0.3, 91.5), 24),
    "wall_east": ((18.0, 2.2, 96.5), (50.0, 0.3, 91.5), 24),
    "pier_landing": ((-43.5, 19.5, 8.5), (-46.5, 12.0, 0.0), 22),
}
STILLS = ("lake_shore", "boathouse", "gate")  # the 34.7 set (the default)


def box_distance(eye, bounds) -> float:
    """Horizontal distance from the eye to a cell's bounds."""
    lo, hi = bounds["min"], bounds["max"]
    dx = max(lo[0] - eye[0], 0.0, eye[0] - hi[0])
    dz = max(lo[2] - eye[2], 0.0, eye[2] - hi[2])
    return math.hypot(dx, dz)


def load_scene(assets: str, eye) -> tuple[list, list, dict]:
    world = os.path.join(assets, "world")
    with open(os.path.join(world, "manifest.json"), encoding="utf-8") as f:
        manifest = json.load(f)
    loaded = [c for c in manifest["cells"] if c.get("resident") or box_distance(eye, c["bounds"]) <= LOAD_RADIUS]
    objs = []
    for c in loaded:
        objs += RT.import_glb(os.path.join(world, c["file"]))
    ids = {c["id"] for c in loaded}
    shown = {}
    hlod = manifest.get("hlod")
    if hlod:
        before = set(bpy.data.objects)
        RT.import_glb(os.path.join(world, hlod["file"]))
        chunks = {c["id"]: c for c in hlod["chunks"]}
        for o in set(bpy.data.objects) - before:
            if o.type != "MESH":
                continue
            cid = o.name.split(".")[1]
            ch = chunks.get(cid)
            visible = ch is not None and not (ch["cells"] and all(c in ids for c in ch["cells"]))
            o.hide_render = not visible
            shown[cid] = visible
            if visible:
                objs.append(o)
    keep = []
    for o in objs:
        if o.get("role") == "camera_collider" or o.get("kit_lod") is not None or o.name.endswith(".collider"):
            o.hide_render = True
            continue
        keep.append(o)
    # The library binds and the toon ramps look a material up by the name before
    # its first "."; an exterior clone (`X@ext`) shares X's textures and ramp.
    for mat in bpy.data.materials:
        if "@ext" in mat.name:
            mat.name = mat.name.replace("@ext", ".ext")
    lights = [lt for c in loaded for lt in c.get("lights", [])]
    print(f"stills: {len(loaded)} cells loaded, hlod chunks shown {sorted(k for k, v in shown.items() if v)}")
    return keep, lights, shown


def render(name: str, hour: float, tag: str, args, toon: dict, lighting: dict) -> str:
    eye, target, lens = VIEWS[name]
    RT.reset("toon", args.samples)
    objs, lights, _ = load_scene(args.assets, eye)
    key = RT.light_key(lighting, hour)
    sun, hemi, sky = key["sun"], key["hemi"], key["sky"]
    RT.set_world(hemi["sky"], hemi["ground"], hemi["intensity"], sky["zenith"], sky["horizon"], False)
    RT.add_sun(sun["direction"], sun["color"], sun["intensity"], shadows=True)
    level = hemi["intensity"] / 0.62
    ambient = [min(1.0, (0.5 + 0.5 * c / 0.85) * level) for c in RT.lin(hemi["sky"])]
    env = {"lref": sun["intensity"] * 0.85, "key": 0.3 + 0.7 * sun["intensity"], "ambient": ambient,
           "key_source": "sun", "soft_scale": 1.0, "point_add": 0.8,
           "rim_colour": RT.lin(lighting["rim"]["color"]), "rim_scale": 0.6}
    for i, lt in enumerate(lights):
        RT.add_point(f"lt_{i}", lt["pos"], lt["color"], lt["intensity"], lt.get("range", 8.0))
    RT.bind_library(objs, args.assets, False, key)
    cache = {}
    for o in objs:
        layer = o.data.color_attributes[0].name if o.data.color_attributes else None
        for slot in o.material_slots:
            orig = slot.material
            if orig is None or orig.get("kit_sky"):
                continue
            k = (orig.name, layer)
            if k not in cache:
                mat = orig.copy()
                RT.toonify(mat, RT.ramp_for(orig.name, toon), layer, env)
                cache[k] = mat
            slot.material = cache[k]
    RT.setup_post(0.0, 0.9, 0.25)
    RT.camera(eye, target, lens)
    scene = bpy.context.scene
    scene.camera.data.clip_end = 3000
    scene.render.resolution_x, scene.render.resolution_y = 1600, 900
    scene.render.resolution_percentage = 100
    path = os.path.join(args.out, f"p3a-{name}-{tag}.png")
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    print(f"rendered {path}")
    return path


def main() -> None:
    repo = RT.REPO
    ap = argparse.ArgumentParser(description="Stills of the castle's silhouette")
    ap.add_argument("--assets", default=os.path.join(repo, "client", "public", "assets"))
    ap.add_argument("--out", default=os.path.join(repo, ".out", "renders"))
    ap.add_argument("--views", default=",".join(STILLS))
    ap.add_argument("--hours", default="13,23", help="day hour, night hour")
    ap.add_argument("--samples", type=int, default=16)
    args = ap.parse_args(script_args(sys.argv))
    with open(os.path.join(repo, "data", "art", "toon_palette.json"), encoding="utf-8") as f:
        toon = json.load(f)
    with open(os.path.join(repo, "data", "art", "lighting.json"), encoding="utf-8") as f:
        lighting = json.load(f)
    os.makedirs(args.out, exist_ok=True)
    hours = [float(h) for h in args.hours.split(",")]
    for name in args.views.split(","):
        for hour, tag in zip(hours, ("day", "night")):
            render(name, hour, tag, args, toon, lighting)


if __name__ == "__main__":
    main()
