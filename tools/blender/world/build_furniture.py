"""Exports every furniture item of data/items/catalog.json for the client.

    blender -b --factory-startup --python tools/blender/world/build_furniture.py -- [--render]

For each furniture entry (models in furniture_models.py, sized from the
catalog) it writes `client/public/assets/<asset>.glb` (asset keys are
`furniture/<name>`), plus:

* `client/public/assets/furniture/manifest.json`: asset key -> file, item
  ids, surface, footprint, height, top, bounds, triangles, draw calls, item
  lights, and the dye convention;
* `art/world/furniture.blend`: every item in a lineup;
* with `--render`: `art/renders/furniture/lineup_<house>.png`, the starter
  set dyed as each house's new characters get it, on a stage.

Each glb holds one mesh with one material (one draw call): the shared
512 x 512 atlas (atlas.py) as base colour, the dye mask as `COLOR_0`
(R = primary, G = secondary, B = trim; black = undyed), and for lanterns an
emissive map lighting the flame.
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

import bpy  # noqa: E402

import atlas  # noqa: E402
import bpyutil  # noqa: E402
import furniture_models as fm  # noqa: E402
import review  # noqa: E402
from castle_kit import fire  # noqa: E402  (animated fire)
from check_layout import script_args  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
CATALOG = os.path.join(REPO, "data", "items", "catalog.json")
PALETTE = os.path.join(REPO, "data", "items", "palette.json")

DYE_RULE = {
    "mask": "COLOR_0",
    "channels": ["primary", "secondary", "trim"],
    "rule": "albedo = baseColor * (1 - r - g - b + r * P + g * S + b * T), P/S/T = the item's palette colours (linear)",
}


def furniture_items() -> list[dict]:
    with open(CATALOG, encoding="utf-8") as f:
        items = json.load(f)["items"]
    return [i for i in items if i["kind"] == "furniture" and not i.get("retired")]


def build_assets() -> dict[str, tuple[list[dict], object]]:
    """Asset key -> (catalog items using it, mesh). Items sharing an asset
    share one model."""
    out: dict[str, tuple[list[dict], object]] = {}
    for item in furniture_items():
        if item["asset"] in out:
            out[item["asset"]][0].append(item)
        elif item["asset"] not in fm.MODELS:
            # A catalog item whose model is not written yet: skipped, like clothing without a builder; the
            # client draws a stand-in until it exists.
            print(f"no model for {item['asset']} (item {item['id']}); skipped")
        else:
            out[item["asset"]] = ([item], fm.build(item))
    return out


def main() -> None:
    import pins  # tools/blender/BLENDER_VERSION

    pins.require_blender()
    ap = argparse.ArgumentParser(description="Export the furniture models")
    ap.add_argument("--assets", default=os.path.join(REPO, "client", "public", "assets"))
    ap.add_argument("--blend", default=os.path.join(REPO, "art", "world", "furniture.blend"))
    ap.add_argument("--renders", default=os.path.join(REPO, "art", "renders", "furniture"))
    ap.add_argument("--render", action="store_true")
    args = ap.parse_args(script_args(sys.argv))

    bpyutil.reset_scene()
    base_img = bpyutil.image_from_array("furniture_atlas", atlas.paint_atlas())
    glow_img = bpyutil.image_from_array("furniture_atlas_emissive", atlas.paint_emissive())
    entries = {}
    objects = {}
    for key, (items, mesh) in build_assets().items():
        name = key.split("/", 1)[1]
        has_light = key in fm.ITEM_LIGHTS
        mat = bpyutil.principled(f"furniture_{name}", image=base_img, roughness=0.62,
                                 emissive=3.0 if has_light else 0.0, emissive_image=glow_img if has_light else None)
        obj = bpyutil.mesh_object(name, mesh, {m: mat for m in mesh.materials()}, uvs=atlas.atlas_uvs(mesh),
                                  colors=atlas.face_masks(mesh))
        path = os.path.join(args.assets, f"{key}.glb")
        # Animated fire (docs/architecture.md): the item's flames in its own frame, as node extras
        # (`fire`, the manifest form) the client reads when it loads the model.
        item_fire = fire.noted(mesh)
        if item_fire:
            obj["fire"] = json.dumps(item_fire, sort_keys=True)
        bpyutil.export_glb([obj], path, vertex_colors=True, extras=bool(item_fire))
        info = bpyutil.glb_info(path)
        lo, hi = mesh.bounds()
        f = items[0]["furniture"]
        entry = {
            "file": f"{name}.glb",
            "item_defs": [i["id"] for i in items],
            "surface": f["surface"],
            "footprint": f["footprint"],
            "height": f["height"],
            "bounds": {"min": [round(v, 3) for v in lo], "max": [round(v, 3) for v in hi]},
            "triangles": info["triangles"],
            "draw_calls": info["draw_calls"],
        }
        if "top" in f:
            entry["top"] = f["top"]
        if "station" in f:
            entry["station"] = f["station"]  # e.g. a cauldron to brew at
        if has_light:
            entry["lights"] = fm.ITEM_LIGHTS[key]
        if item_fire:
            entry["fire"] = item_fire
        entries[key] = entry
        objects[key] = obj
        print(f"{key}: {info['triangles']} triangles, {info['draw_calls']} draw call(s), attributes {info['attributes']}")
    manifest = {"version": 1, "atlas": {"size": atlas.SIZE}, "dye": DYE_RULE, "items": dict(sorted(entries.items()))}
    out = os.path.join(args.assets, "furniture", "manifest.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")
    print(f"wrote {out}")

    lineup(objects)
    if args.blend:
        os.makedirs(os.path.dirname(args.blend), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.blend))
    if args.render:
        render_lineups(objects, args.renders)


# Lineup positions (x, y, z, yaw) on the stage: floor items in a row, table
# items on the tables, wall items on the back wall (z = -1.6).
LINEUP = {
    "furniture/bed_four_poster_a": (-4.6, 0.0, -0.3, 0.5),
    "furniture/bookcase_oak_a": (-2.6, 0.0, -1.38, 0.0),
    "furniture/desk_writing_a": (-1.0, 0.0, -0.9, 0.0),
    "furniture/chair_wooden_a": (-1.0, 0.0, -0.25, math.pi + 0.3),
    "furniture/armchair_velvet_a": (0.6, 0.0, -0.4, -0.4),
    "furniture/trunk_school_a": (2.0, 0.0, -0.5, 0.2),
    "furniture/table_bedside_a": (3.2, 0.0, -0.6, 0.0),
    "furniture/rug_round_a": (1.2, 0.0, 0.9, 0.0),
    "furniture/lantern_candle_a": (-0.75, 0.76, -0.95, 0.4),
    "furniture/cage_owl_a": (3.2, 0.65, -0.6, 0.6),
    "furniture/banner_house_a": (-0.3, 1.1, -1.6, 0.0),
    "furniture/banner_house_b": (0.8, 1.1, -1.6, 0.0),
    "furniture/banner_house_c": (1.9, 1.1, -1.6, 0.0),
    "furniture/banner_house_d": (3.0, 1.1, -1.6, 0.0),
    "furniture/painting_landscape_a": (-1.0, 1.6, -1.6, 0.0),
    "furniture/cauldron_pewter_a": (-2.4, 0.0, 1.0, 0.3),
    "furniture/stool_three_leg_a": (-1.5, 0.0, 1.1, 0.0),
    "furniture/table_round_side_a": (-0.4, 0.0, 1.3, 0.0),
    "furniture/candelabra_brass_a": (-0.4, 0.7, 1.3, 0.2),
    "furniture/fern_potted_a": (2.9, 0.0, 0.9, 0.0),
    "furniture/herb_pot_a": (2.0, 0.55, -0.5, 0.0),
}


def lineup(objects: dict) -> None:
    for key, obj in objects.items():
        x, y, z, yaw = LINEUP.get(key, (0.0, 0.0, 2.0, 0.0))
        obj.location = bpyutil.to_blender((x, y, z))
        obj.rotation_euler = (0.0, 0.0, yaw)


def granted(item: dict, house: str, palette: dict) -> list[str]:
    hex_of = {c["index"]: c["hex"] for c in palette["colours"]}
    out = []
    for ch in ("primary", "secondary", "trim"):
        idx = item["dyes"][ch]
        choice = (item.get("house_dyes") or {}).get(ch)
        if choice:
            idx = palette["houses"][house]["primary" if choice == "house_primary" else "secondary"]
        out.append(hex_of[idx])
    return out


def render_lineups(objects: dict, out_dir: str) -> None:
    import meshkit as mk

    with open(PALETTE, encoding="utf-8") as f:
        palette = json.load(f)
    items = {i["asset"]: i for i in furniture_items()}
    # A simple stage: floor and back wall in calm neutral stone.
    stage = mk.Mesh()
    mk.box(stage, (-6.5, -0.05, -1.6), (5.0, 0.0, 2.5), "stage", skip=("-y",))
    stage.poly([(-6.5, 0.0, -1.6), (5.0, 0.0, -1.6), (5.0, 3.5, -1.6), (-6.5, 3.5, -1.6)], "wall")
    stage_mats = {"stage": bpyutil.principled("stage", "#7A7469", roughness=0.9),
                  "wall": bpyutil.principled("stage_wall", "#8C8273", roughness=0.9)}
    bpyutil.mesh_object("stage", stage, stage_mats)
    review.setup("#B0B4BC", 0.6)
    review.point_light("key", (-2.0, 3.6, 3.0), "#FFF2DC", 40.0, 1.0)
    review.point_light("fill", (3.5, 2.5, 2.5), "#C4D4EE", 16.0, 1.0)
    review.point_light("lantern", (-0.75, 0.97, -0.95), "#FFC877", 1.0, 0.05)
    os.makedirs(out_dir, exist_ok=True)
    base = {key: obj.material_slots[0].material for key, obj in objects.items()}
    for house in palette["houses"]:
        for key, obj in objects.items():
            obj.material_slots[0].material = review.dyed_preview_material(base[key], granted(items[key], house, palette))
        slot = "house_" + chr(ord("a") + list(palette["houses"]).index(house))
        review.camera((-0.5, 2.6, 6.2), (-0.5, 0.75, -0.6), 26)
        review.render(os.path.join(out_dir, f"lineup_{slot}.png"), 1600, 900)
    review.camera((-3.4, 1.6, 2.4), (-3.6, 0.9, -0.6), 30)
    review.render(os.path.join(out_dir, "closeup_bed_bookcase.png"), 1280, 800)
    review.camera((1.2, 1.3, 2.2), (1.4, 0.6, -0.6), 30)
    review.render(os.path.join(out_dir, "closeup_seating.png"), 1280, 800)
    review.camera((-1.4, 1.2, 3.4), (-1.4, 0.4, 1.1), 34)
    review.render(os.path.join(out_dir, "closeup_cauldron_stool_table.png"), 1280, 800)
    review.camera((2.6, 0.9, 2.6), (2.5, 0.45, 0.4), 32)
    review.render(os.path.join(out_dir, "closeup_plants.png"), 1280, 800)
    for key, obj in objects.items():
        obj.material_slots[0].material = base[key]


if __name__ == "__main__":
    main()
