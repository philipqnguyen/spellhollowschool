"""Builds the spell effects and the wand (`vfx_models.py`) and exports them
for the client.

    blender -b --factory-startup --python tools/blender/vfx/build_vfx.py -- \\
        [--assets client/public/assets] [--render art/renders/vfx] [--blend art/vfx/vfx.blend]

Writes, under `<assets>/vfx/`:

* `effects.glb`: every effect mesh as its own node (`spell_<id>_<role>`),
  all sharing one unlit, alpha-blended, double-sided material (`vfx`)
  whose base colour is `atlas.png` and whose `COLOR_0` carries the colour
  (glTF multiplies the two); the client draws them additively and
  instances them (thin instances per node);
* `wand.glb`: the wand, one opaque mesh with a 64 px swatch texture,
  origin at the grip, shaft along +Y;
* `atlas.png`: the 256 px effect atlas (also embedded in effects.glb);
* `manifest.json`: `{ version, generator, effects: "vfx/effects.glb",
  atlas: { file, size, grid, tiles }, blend: "additive", spells: { "<spell
  id>": { colour, shape?, core?, trail?, impact?, charge?, disarm?,
  shield?, flash?, glow?, sparkle? } }, meshes: { "<node>": { triangles,
  bounds } }, wand: { file, grip: [0, 0, 0], tip: [0, 0.26, 0], attach: {
  "<body id>": { bone: "socket_hand.R", rotation: [x, y, z, w],
  translation: [x, y, z] } } } }`. `attach` is the wand node's transform
  as a child of the body's `socket_hand.R` joint, read from the exported
  body files (`characters/<body>.glb`), so the tip lands where the
  character clips put it; it is left out (with a warning) until the
  characters are built.

`--render` writes a contact sheet of every effect and close-ups of the wand
in a hand into the given folder (e.g. art/renders/vfx, gitignored).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.dont_write_bytecode = True
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), "world"), os.path.join(os.path.dirname(HERE), "characters"),
                os.path.dirname(HERE)]

import bpy  # noqa: E402
from mathutils import Matrix, Quaternion, Vector  # noqa: E402

import bpyutil  # noqa: E402
import vfx_models as vm  # noqa: E402
from check_layout import script_args  # noqa: E402

REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
MANIFEST_VERSION = 1
BODIES = ("body_a", "body_b")


def rgba_image(name: str, arr) -> bpy.types.Image:
    h, w, _ = arr.shape
    img = bpy.data.images.new(name, w, h, alpha=True)
    img.colorspace_settings.name = "sRGB"
    img.pixels.foreach_set(arr.astype("float32").ravel())
    img.file_format = "PNG"
    img.alpha_mode = "STRAIGHT"
    img.pack()
    return img


def fx_material(image: bpy.types.Image) -> bpy.types.Material:
    """Unlit (KHR_materials_unlit), alpha-blended, double-sided: the
    exporter's 'light path trick' layout with a mix-with-transparent for
    alpha; colour = atlas x COLOR_0."""
    mat = bpy.data.materials.new("vfx")
    if mat.node_tree is None:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.interpolation = "Linear"
    vc = nt.nodes.new("ShaderNodeVertexColor")
    vc.layer_name = "COLOR_0"
    mul = nt.nodes.new("ShaderNodeMix")
    mul.data_type = "RGBA"
    mul.blend_type = "MULTIPLY"
    mul.inputs["Factor"].default_value = 1.0
    nt.links.new(tex.outputs["Color"], mul.inputs["A"])
    nt.links.new(vc.outputs["Color"], mul.inputs["B"])
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = 1.0
    nt.links.new(mul.outputs["Result"], emit.inputs["Color"])
    lp = nt.nodes.new("ShaderNodeLightPath")
    inner = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(lp.outputs["Is Camera Ray"], inner.inputs[0])
    nt.links.new(emit.outputs["Emission"], inner.inputs[1])
    nt.links.new(emit.outputs["Emission"], inner.inputs[2])
    transp = nt.nodes.new("ShaderNodeBsdfTransparent")
    outer = nt.nodes.new("ShaderNodeMixShader")
    nt.links.new(tex.outputs["Alpha"], outer.inputs[0])
    nt.links.new(transp.outputs["BSDF"], outer.inputs[1])
    nt.links.new(inner.outputs["Shader"], outer.inputs[2])
    nt.links.new(outer.outputs["Shader"], out.inputs["Surface"])
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    else:
        mat.blend_method = "BLEND"
    mat.use_backface_culling = False
    return mat


def read_glb_json(path: str) -> dict:
    with open(path, "rb") as f:
        data = f.read()
    (json_len,) = struct.unpack_from("<I", data, 12)
    return json.loads(data[20 : 20 + json_len])


def node_world(doc: dict, name: str) -> Matrix | None:
    """The rest world matrix of a named glTF node (product of local TRS up
    the parent chain), in glTF axes."""
    nodes = doc.get("nodes", [])
    parent = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
    idx = next((i for i, n in enumerate(nodes) if n.get("name") == name), None)
    if idx is None:
        return None
    mat = Matrix.Identity(4)
    while idx is not None:
        n = nodes[idx]
        if "matrix" in n:
            local = Matrix([n["matrix"][i::4] for i in range(4)])
        else:
            t = Vector(n.get("translation", (0, 0, 0)))
            x, y, z, w = n.get("rotation", (0, 0, 0, 1))
            s = Vector(n.get("scale", (1, 1, 1)))
            local = Matrix.LocRotScale(t, Quaternion((w, x, y, z)), s)
        mat = local @ mat
        idx = parent.get(idx)
    return mat


def to_gltf(v) -> Vector:
    """Blender Z-up rig space -> glTF Y-up."""
    return Vector((v[0], v[2], -v[1]))


def wand_attach(assets: str) -> dict:
    """The wand's transform under `socket_hand.R` per body, so its local +Y
    (the shaft) runs along the character clips' wand direction from the
    grip, with its local +X toward the palm."""
    from charlib import anim_spells as SP
    from charlib import proportions as P

    out = {}
    for body_id in BODIES:
        path = os.path.join(assets, "characters", f"{body_id}.glb")
        if not os.path.exists(path):
            print(f"[vfx] warning: {path} missing; wand attach for {body_id} left out (build the characters first)")
            continue
        sock = node_world(read_glb_json(path), "socket_hand.R")
        if sock is None:
            print(f"[vfx] warning: {path} has no socket_hand.R")
            continue
        g = SP.Grip(P.get(body_id))
        y = to_gltf(g.wand).normalized()
        x = to_gltf(g.palm)
        x = (x - y * x.dot(y)).normalized()
        z = x.cross(y)
        want = Matrix((x, y, z)).transposed().to_4x4()
        want.translation = to_gltf(g.grip)
        local = sock.inverted() @ want
        loc, rot, _ = local.decompose()
        # Check: the tip lands where the clips solve it.
        tip = sock @ local @ Vector((0.0, SP.WAND_TIP, 0.0))
        expect = to_gltf(g.grip + g.wand * SP.WAND_TIP)
        err = (tip - expect).length
        if err > 1e-3:
            raise RuntimeError(f"wand attach for {body_id} misses the tip by {err:.4f} m")
        out[body_id] = {"bone": "socket_hand.R", "rotation": [round(v, 6) for v in (rot.x, rot.y, rot.z, rot.w)],
                        "translation": [round(v, 5) for v in loc]}
    return out


def build(args) -> dict:
    bpyutil.reset_scene()
    vfx_dir = os.path.join(args.assets, "vfx")
    os.makedirs(vfx_dir, exist_ok=True)
    atlas = vm.paint_atlas()
    image = rgba_image("vfx_atlas", atlas)
    mat = fx_material(image)
    meshes, table = vm.effects()
    objs, info = [], {}
    for name, fx in sorted(meshes.items()):
        if len(fx.colours) != len(fx.faces):
            raise RuntimeError(f"{name}: {len(fx.colours)} colours for {len(fx.faces)} faces")
        obj = bpyutil.mesh_object(name, fx, {"fx": mat}, colors=fx.colours)
        objs.append(obj)
        lo, hi = fx.bounds()
        info[name] = {"triangles": fx.triangle_count(), "bounds": {"min": [round(v, 3) for v in lo],
                                                                   "max": [round(v, 3) for v in hi]}}
    bpyutil.export_glb(objs, os.path.join(vfx_dir, "effects.glb"), vertex_colors=True)
    # The atlas as its own file too (for the client's own materials).
    image.filepath_raw = os.path.join(vfx_dir, "atlas.png")
    image.save()
    # The wand.
    wand_img = rgba_image("wand_swatches", vm.wand_texture())
    wand_mat = bpyutil.principled("wand", "#FFFFFF", wand_img, 0.45)
    wand = bpyutil.mesh_object("wand", vm.wand_mesh(), {"wand": wand_mat})
    bpyutil.export_glb([wand], os.path.join(vfx_dir, "wand.glb"))
    manifest = {
        "version": MANIFEST_VERSION,
        "generator": "tools/blender/vfx/build_vfx.py",
        "effects": "vfx/effects.glb",
        "atlas": {"file": "vfx/atlas.png", "size": vm.ATLAS_SIZE, "grid": vm.ATLAS_GRID, "tiles": vm.ATLAS_TILES},
        "blend": "additive",
        "formula": "colour = atlas.rgb * COLOR_0 * atlas.a (unlit, additive, double-sided)",
        "spells": table,
        "meshes": info,
        "wand": {"file": "vfx/wand.glb", "grip": [0.0, 0.0, 0.0], "tip": [0.0, vm.WAND_TIP, 0.0],
                 "butt": [0.0, -vm.WAND_BUTT, 0.0], "attach": wand_attach(args.assets)},
    }
    with open(os.path.join(vfx_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    tris = sum(v["triangles"] for v in info.values())
    print(f"[vfx] {len(objs)} effect meshes, {tris} triangles, one material, atlas {vm.ATLAS_SIZE} px -> {vfx_dir}")
    if args.blend:
        os.makedirs(os.path.dirname(os.path.abspath(args.blend)), exist_ok=True)
        bpy.context.preferences.filepaths.save_version = 0
        bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.blend), compress=True)
    if args.render:
        render_sheet(objs, table, args.render)
    return manifest


def review_material(objs) -> None:
    """For the review renders only: the effects drawn additively (emission
    times alpha over transparency), the way the client blends them."""
    src = objs[0].data.materials[0]
    mat = src.copy()
    nt = mat.node_tree
    tex = next(n for n in nt.nodes if n.type == "TEX_IMAGE")
    mul = next(n for n in nt.nodes if n.type == "MIX")
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    scale = nt.nodes.new("ShaderNodeMix")
    scale.data_type = "RGBA"
    scale.blend_type = "MULTIPLY"
    scale.inputs["Factor"].default_value = 1.0
    nt.links.new(mul.outputs["Result"], scale.inputs["A"])
    nt.links.new(tex.outputs["Alpha"], scale.inputs["B"])
    emit = nt.nodes.new("ShaderNodeEmission")
    emit.inputs["Strength"].default_value = 1.6
    nt.links.new(scale.outputs["Result"], emit.inputs["Color"])
    add = nt.nodes.new("ShaderNodeAddShader")
    transp = nt.nodes.new("ShaderNodeBsdfTransparent")
    nt.links.new(transp.outputs["BSDF"], add.inputs[0])
    nt.links.new(emit.outputs["Emission"], add.inputs[1])
    nt.links.new(add.outputs["Shader"], out.inputs["Surface"])
    for o in objs:
        o.data.materials[0] = mat


def render_sheet(objs, table: dict, out_dir: str) -> None:
    """Every effect on a dark backdrop, one tile each, labelled by spell and role."""
    import review
    from charlib import montage

    os.makedirs(out_dir, exist_ok=True)
    scene = bpy.context.scene
    review.setup("#141826", 0.2)
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    for o in scene.objects:
        o.hide_render = True
    review_material(objs)
    paths = []
    for o in objs:
        o.hide_render = False
        lo, hi = Vector(o.bound_box[0]), Vector(o.bound_box[6])
        centre = (lo + hi) / 2
        size = max((hi - lo).length, 0.3)
        # Three-quarter view from the side and a little ahead (glTF +Z = Blender -Y).
        eye = centre + Vector((size * 1.1, -size * 0.9, size * 0.55))
        cam = bpy.data.cameras.new("cam")
        cam.lens = 40
        cam_obj = bpy.data.objects.new("cam", cam)
        scene.collection.objects.link(cam_obj)
        cam_obj.location = eye
        cam_obj.rotation_euler = (centre - eye).to_track_quat("-Z", "Y").to_euler()
        scene.camera = cam_obj
        path = os.path.join(out_dir, f"_{o.name}.png")
        review.render(path, 320, 320)
        paths.append(path)
        o.hide_render = True
        bpy.data.objects.remove(cam_obj)
    montage.tile(paths, os.path.join(out_dir, "effects.png"), 6, remove=True)
    print(f"[vfx] contact sheet -> {os.path.join(out_dir, 'effects.png')} ({[o.name for o in objs]})")


def main() -> None:
    import pins  # tools/blender/BLENDER_VERSION

    pins.require_blender()
    ap = argparse.ArgumentParser(description="Build the spell effects and the wand")
    ap.add_argument("--assets", default=os.path.join(REPO, "client", "public", "assets"))
    ap.add_argument("--render", default=None, help="write review renders to this directory")
    ap.add_argument("--blend", default=None, help="also save the built scene (e.g. art/vfx/vfx.blend)")
    args = ap.parse_args(script_args(sys.argv))
    args.assets = os.path.abspath(args.assets)
    build(args)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback

        traceback.print_exc()
        sys.exit(1)
