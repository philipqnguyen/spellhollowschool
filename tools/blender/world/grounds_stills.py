"""Review stills of the grounds (docs/art/castle-kit.md "Exterior"),
by day and by night, through render_toon.py's toon preview of the exported world.

    blender -b --factory-startup --python tools/blender/world/grounds_stills.py -- \\
        [--assets client/public/assets] [--out .out/renders] [--views gate,shore] [--hours 13,23]

Writes `<out>/grounds_<view>_<day|night>.png`. On top of render_toon.py it gives
the grounds' own materials a preview of what the client builds by name:
`grounds_terrain` the turf texture mixed toward the palette's `grounds_path` and
`grounds_rock` by the TEXCOORD_1 mask, `lake_water` the palette's `lake_shallow`
to `lake_deep` by the COLOR_0 depth with faint `lake_band` bands; and binds the
library textures to the exterior clones (`castle_wall_c@ext` takes
`castle_wall_c`'s). An approximation for judging the layout and the colours,
not the client's shaders.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [HERE, os.path.dirname(HERE)]

import bpy  # noqa: E402

import render_toon as RT  # noqa: E402
from check_layout import script_args  # noqa: E402

REPO = RT.REPO
# name -> (kind, eye, target, lens mm, exterior)
VIEWS = {
    "gate": ("castle", (16.0, 5.0, 95.0), (14.0, -10.0, 190.0), 18, True),
    "shore": ("castle", (34.0, -12.9, 253.0), (-80.0, -11.0, 214.0), 20, True),
    "hut": ("castle", (80.0, -4.6, 164.0), (100.0, -4.0, 178.0), 22, True),
    "boathouse": ("castle", (-76.0, -11.8, 214.0), (-99.0, -12.5, 203.0), 22, True),
    "overview": ("castle", (190.0, 70.0, 330.0), (10.0, -6.0, 170.0), 22, True),
}
HOURS = {"day": 13.0, "night": 23.0}


def _palette():
    with open(os.path.join(REPO, "data", "art", "castle_textures.json"), encoding="utf-8") as f:
        return {k: v for k, v in json.load(f)["palette"].items() if not k.startswith("_")}


def _rgb(nt, hex_colour):
    n = nt.nodes.new("ShaderNodeRGB")
    n.outputs[0].default_value = (*RT.lin(hex_colour), 1.0)
    return n.outputs[0]


def _mix(nt, a, b, fac):
    m = nt.nodes.new("ShaderNodeMix")
    m.data_type = "RGBA"
    nt.links.new(fac, m.inputs["Factor"])
    nt.links.new(a, m.inputs["A"])
    nt.links.new(b, m.inputs["B"])
    return m.outputs["Result"]


def terrain_material(mat, assets: str, pal: dict, uv1: str) -> None:
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = bpy.data.images.load(os.path.join(assets, "world", "textures", "grass_turf.png"), check_existing=True)
    uvm = nt.nodes.new("ShaderNodeUVMap")
    uvm.uv_map = uv1
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(uvm.outputs["UV"], sep.inputs[0])
    rock = nt.nodes.new("ShaderNodeMath")  # glTF v (rock) = 1 - Blender v
    rock.operation = "SUBTRACT"
    rock.inputs[0].default_value = 1.0
    nt.links.new(sep.outputs["Y"], rock.inputs[1])
    col = _mix(nt, tex.outputs["Color"], _rgb(nt, pal["grounds_path"]), sep.outputs["X"])
    col = _mix(nt, col, _rgb(nt, pal["grounds_rock"]), rock.outputs[0])
    nt.links.new(col, bsdf.inputs["Base Color"])


def water_material(mat, pal: dict, layer: str) -> None:
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    vc = nt.nodes.new("ShaderNodeVertexColor")
    vc.layer_name = layer
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    nt.links.new(vc.outputs["Color"], sep.inputs["Color"])
    col = _mix(nt, _rgb(nt, pal["lake_shallow"]), _rgb(nt, pal["lake_deep"]), sep.outputs[0])
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.inputs["Scale"].default_value = 0.6
    wave.inputs["Distortion"].default_value = 3.0
    ramp = nt.nodes.new("ShaderNodeMapRange")
    ramp.inputs["From Min"].default_value = 0.75
    ramp.inputs["From Max"].default_value = 1.0
    ramp.inputs["To Max"].default_value = 0.35
    nt.links.new(wave.outputs["Fac"], ramp.inputs["Value"])
    col = _mix(nt, col, _rgb(nt, pal["lake_band"]), ramp.outputs["Result"])
    nt.links.new(col, bsdf.inputs["Base Color"])


def bind_ext(objs, assets: str) -> None:
    """The exterior clones (`<name>@ext`) take their base row's library image."""
    lib_path = os.path.join(assets, "world", "textures", "textures.json")
    with open(lib_path, encoding="utf-8") as f:
        lib = json.load(f).get("materials", {})
    done = set()
    for o in objs:
        for slot in o.material_slots:
            mat = slot.material
            if mat is None or mat.name in done or "@ext" not in mat.name:
                continue
            done.add(mat.name)
            entry = lib.get(mat.name.split(".")[0].replace("@ext", ""))
            if not entry:
                continue
            nt = mat.node_tree
            bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = bpy.data.images.load(os.path.join(assets, "world", "textures", entry["base"]), check_existing=True)
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])


_bind = RT.bind_library


def bind_library(objs, assets, preview, key):
    _bind(objs, assets, preview, key)
    bind_ext(objs, assets)
    pal = _palette()
    done = set()
    for o in objs:
        for slot in o.material_slots:
            mat = slot.material
            if mat is None or mat.name in done:
                continue
            base = mat.name.split(".")[0]
            if base == "grounds_terrain" and len(o.data.uv_layers) > 1:
                terrain_material(mat, assets, pal, o.data.uv_layers[1].name)
                done.add(mat.name)
            elif base == "lake_water" and o.data.color_attributes:
                water_material(mat, pal, o.data.color_attributes[0].name)
                done.add(mat.name)


def main() -> None:
    ap = argparse.ArgumentParser(description="Grounds review stills")
    ap.add_argument("--assets", default=os.path.join(REPO, "client", "public", "assets"))
    ap.add_argument("--out", default=os.path.join(REPO, ".out", "renders"))
    ap.add_argument("--views", default=",".join(VIEWS))
    ap.add_argument("--times", default="day,night")
    ap.add_argument("--samples", type=int, default=16)
    args = ap.parse_args(script_args(sys.argv))
    RT.bind_library = bind_library
    with open(os.path.join(REPO, "data", "art", "toon_palette.json"), encoding="utf-8") as f:
        toon = json.load(f)
    with open(os.path.join(REPO, "data", "art", "lighting.json"), encoding="utf-8") as f:
        lighting = json.load(f)
    os.makedirs(args.out, exist_ok=True)
    for t in args.times.split(","):
        ns = argparse.Namespace(assets=args.assets, out=args.out, prefix=f"grounds_{t}", shading="toon",
                                hour=HOURS[t], samples=args.samples, line=1.0, kit_preview=False)
        for name in args.views.split(","):
            RT.render_view(name, ns, toon, lighting, VIEWS)
            src = os.path.join(args.out, f"grounds_{t}_{name}.png")
            dst = os.path.join(args.out, f"grounds_{name}_{t}.png")
            os.replace(src, dst)
            print(f"still {dst}")


if __name__ == "__main__":
    main()
